from __future__ import annotations

import sys
from typing import Any

from dacpactools.domain.column_usage import ColumnCitation, ColumnUsageContext
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.infrastructure.scriptdom.ast_utils import (
    identifier_value,
    multipart_parts,
    schema_object_parts,
    script_text,
    type_name,
)
from dacpactools.infrastructure.scriptdom.expression_resolver import (
    QueryScope,
    TableBinding,
)

_AGGREGATE_FUNCS: frozenset[str] = frozenset(
    {
        "SUM",
        "COUNT",
        "COUNT_BIG",
        "AVG",
        "MIN",
        "MAX",
        "STDEV",
        "STDEVP",
        "VAR",
        "VARP",
        "STRING_AGG",
    }
)


def find_column_usage(
    consumer: ObjectRef,
    fragment: Any,
    target_owner: ObjectRef,
    target_column: str,
) -> tuple[list[ColumnCitation], list[str]]:
    """Scan the parsed script for every reference to
    target_owner.target_column, tagged by clause context."""
    if fragment is None:
        return [], []
    # Deeply-nested SQL (e.g. long IF/ELSE chains, massive CASE trees) can
    # exceed the default 1000-frame Python recursion limit during AST walk.
    prev_limit = sys.getrecursionlimit()
    if prev_limit < 10000:
        sys.setrecursionlimit(10000)
    try:
        finder = _Finder(
            consumer=consumer,
            target_owner=target_owner,
            target_column=target_column.lower(),
        )
        finder.visit(fragment)
        return finder.citations, finder.warnings
    finally:
        sys.setrecursionlimit(prev_limit)


class _Finder:
    def __init__(
        self,
        consumer: ObjectRef,
        target_owner: ObjectRef,
        target_column: str,
    ) -> None:
        self.consumer = consumer
        self.target_owner = target_owner
        self.target_column = target_column
        self._target_key = (
            target_owner.schema_name.lower(),
            target_owner.name.lower(),
        )
        self.citations: list[ColumnCitation] = []
        self.warnings: list[str] = []
        self._scope_stack: list[QueryScope] = []
        self._context_stack: list[ColumnUsageContext] = []
        self._stmt_kind: str = ""

    # ------------------------------------------------------------ entry

    def visit(self, node: Any) -> None:
        if node is None:
            return
        t = type_name(node)
        # Statement-level dispatch
        if t == "TSqlScript":
            for b in node.Batches or []:
                self.visit(b)
            return
        if t == "TSqlBatch":
            for s in b_stmts(node):
                self.visit(s)
            return
        if t in {
            "CreateProcedureStatement",
            "CreateOrAlterProcedureStatement",
            "AlterProcedureStatement",
        }:
            self._stmt_kind = "PROCEDURE"
            self._visit_stmt_list(getattr(node, "StatementList", None))
            return
        if t in {
            "CreateFunctionStatement",
            "CreateOrAlterFunctionStatement",
            "AlterFunctionStatement",
        }:
            self._stmt_kind = "FUNCTION"
            self._visit_stmt_list(getattr(node, "StatementList", None))
            inline = getattr(node, "ReturnType", None)
            if inline is not None:
                sel = getattr(inline, "SelectStatement", None)
                if sel is not None:
                    self.visit(sel)
            return
        if t in {
            "CreateViewStatement",
            "CreateOrAlterViewStatement",
            "AlterViewStatement",
        }:
            self._stmt_kind = "VIEW"
            sel = getattr(node, "SelectStatement", None)
            if sel is not None:
                self.visit(sel)
            return
        if t == "BeginEndBlockStatement":
            self._visit_stmt_list(getattr(node, "StatementList", None))
            return
        if t == "TryCatchStatement":
            self._visit_stmt_list(getattr(node, "TryStatements", None))
            self._visit_stmt_list(getattr(node, "CatchStatements", None))
            return
        if t in {"IfStatement", "WhileStatement"}:
            for a in ("ThenStatement", "Statement", "ElseStatement"):
                inner = getattr(node, a, None)
                if inner is not None:
                    self.visit(inner)
            return
        if t == "CreateTableStatement":
            sel = getattr(node, "SelectStatement", None)
            if sel is not None:
                prev = self._stmt_kind
                self._stmt_kind = "CTAS"
                self.visit(sel)
                self._stmt_kind = prev
            return
        if t == "SelectStatement":
            prev = self._stmt_kind
            self._stmt_kind = self._stmt_kind or "SELECT"
            self._visit_select_statement(node)
            self._stmt_kind = prev
            return
        if t == "InsertStatement":
            prev = self._stmt_kind
            self._stmt_kind = "INSERT"
            self._visit_insert(node)
            self._stmt_kind = prev
            return
        if t == "UpdateStatement":
            prev = self._stmt_kind
            self._stmt_kind = "UPDATE"
            self._visit_update(node)
            self._stmt_kind = prev
            return
        if t == "MergeStatement":
            prev = self._stmt_kind
            self._stmt_kind = "MERGE"
            self._visit_merge(node)
            self._stmt_kind = prev
            return
        # Unknown statement — try common containers so we don't miss nested
        # SELECTs (e.g. inside CTE with clauses).
        for attr in ("StatementList", "SelectStatement"):
            v = getattr(node, attr, None)
            if v is not None:
                self.visit(v)

    def _visit_stmt_list(self, sl: Any) -> None:
        if sl is None:
            return
        for s in sl.Statements:
            self.visit(s)

    # ------------------------------------------------------------ SELECT

    def _visit_select_statement(self, sel: Any) -> None:
        # CTEs: warn if target table is referenced inside them (v1 skips CTE
        # reprojection tracking). But still walk them so we catch citations
        # in the CTE body itself.
        wc = getattr(sel, "WithCtesAndXmlNamespaces", None)
        if wc is not None:
            for cte in getattr(wc, "CommonTableExpressions", []) or []:
                name = identifier_value(cte.ExpressionName) or "?"
                inner = getattr(cte, "QueryExpression", None)
                if inner is not None:
                    before = len(self.citations)
                    self.visit_query_expression(inner)
                    if len(self.citations) > before:
                        self.warnings.append(
                            f"target column referenced inside CTE '{name}' — "
                            f"downstream reprojection not tracked"
                        )
        self.visit_query_expression(sel.QueryExpression)
        # ORDER BY on SelectStatement — reuses the outermost QuerySpec scope
        ob = getattr(sel, "OrderByClause", None)
        if ob is not None and self._scope_stack:
            self._walk_order_by(ob, ColumnUsageContext.ORDER_BY)

    def visit_query_expression(self, qe: Any) -> None:
        t = type_name(qe)
        if t == "QuerySpecification":
            self._visit_query_spec(qe)
            return
        if t == "BinaryQueryExpression":
            self.visit_query_expression(qe.FirstQueryExpression)
            self.visit_query_expression(qe.SecondQueryExpression)
            return
        if t == "QueryParenthesisExpression":
            self.visit_query_expression(qe.QueryExpression)
            return

    def _visit_query_spec(self, qs: Any) -> None:
        scope = self._build_scope(qs.FromClause)
        self._scope_stack.append(scope)
        try:
            for elem in qs.SelectElements:
                self._map_select_element(elem)
            if qs.FromClause is not None:
                for tref in qs.FromClause.TableReferences:
                    self._walk_join_conditions(tref)
            self._walk_clause(
                getattr(qs, "WhereClause", None), ColumnUsageContext.WHERE
            )
            gb = getattr(qs, "GroupByClause", None)
            if gb is not None:
                for spec in getattr(gb, "GroupingSpecifications", []) or []:
                    expr = getattr(spec, "Expression", spec)
                    self._push_walk(expr, ColumnUsageContext.GROUP_BY)
            self._walk_clause(
                getattr(qs, "HavingClause", None), ColumnUsageContext.HAVING
            )
        finally:
            self._scope_stack.pop()

    def _build_scope(self, from_clause: Any) -> QueryScope:
        scope = QueryScope()
        if from_clause is None:
            return scope
        for tref in from_clause.TableReferences:
            self._collect_bindings(tref, scope)
        return scope

    def _collect_bindings(self, table_ref: Any, scope: QueryScope) -> None:
        t = type_name(table_ref)
        if t == "NamedTableReference":
            db, schema, name = schema_object_parts(table_ref.SchemaObject)
            if not name:
                return
            alias = identifier_value(getattr(table_ref, "Alias", None)) or name
            source_ref = ObjectRef(database=db, schema=schema or "dbo", name=name)
            scope.add(
                TableBinding(alias=alias, source_ref=source_ref, columns=())
            )
            return
        if t in {"QualifiedJoin", "UnqualifiedJoin"}:
            self._collect_bindings(table_ref.FirstTableReference, scope)
            self._collect_bindings(table_ref.SecondTableReference, scope)
            return
        if t == "QueryDerivedTable":
            alias = identifier_value(getattr(table_ref, "Alias", None)) or ""
            # Recurse into subquery so its own scopes get walked.
            inner = getattr(table_ref, "QueryExpression", None)
            if inner is not None:
                self.visit_query_expression(inner)
            # Register a placeholder binding so unqualified target columns
            # inside outer references aren't mistakenly attributed.
            scope.add(
                TableBinding(alias=alias, source_ref=None, columns=())
            )

    # -------------------------------------------------- select elements

    def _map_select_element(self, elem: Any) -> None:
        t = type_name(elem)
        if t == "SelectScalarExpression":
            self._push_walk(elem.Expression, ColumnUsageContext.PROJECTION)
            return
        if t == "SelectStarExpression":
            # * expansion — flag only if the star qualifier points to target.
            qual = getattr(elem, "Qualifier", None)
            if qual is not None:
                parts = multipart_parts(qual)
                prefix = parts[-1] if parts else None
                if prefix and self._prefix_matches_target(prefix):
                    self.warnings.append(
                        f"'{prefix}.*' may include target column — not "
                        f"individually tracked in v1"
                    )
            else:
                # Bare * — if any binding matches target, warn.
                for scope in self._scope_stack[-1:]:
                    for b in scope.bindings.values():
                        if self._binding_matches_target(b):
                            self.warnings.append(
                                "unqualified '*' may include target column — "
                                "not individually tracked in v1"
                            )
                            break
            return

    # -------------------------------------------------- join walkers

    def _walk_join_conditions(self, table_ref: Any) -> None:
        t = type_name(table_ref)
        if t == "QualifiedJoin":
            cond = getattr(table_ref, "SearchCondition", None)
            if cond is not None:
                self._push_walk(cond, ColumnUsageContext.JOIN_ON)
            self._walk_join_conditions(table_ref.FirstTableReference)
            self._walk_join_conditions(table_ref.SecondTableReference)
            return
        if t == "UnqualifiedJoin":
            self._walk_join_conditions(table_ref.FirstTableReference)
            self._walk_join_conditions(table_ref.SecondTableReference)
            return

    # -------------------------------------------------- INSERT/UPDATE/MERGE

    def _visit_insert(self, ins: Any) -> None:
        spec = getattr(ins, "InsertSpecification", ins)
        target_ref = getattr(spec, "Target", None)
        target_ident = self._named_table_ref_ident(target_ref)
        # Target column list -> ASSIGN_TARGET
        for col in getattr(spec, "Columns", []) or []:
            parts = multipart_parts(col.MultiPartIdentifier)
            if not parts:
                continue
            if parts[-1].lower() == self.target_column and target_ident == self._target_key:
                self._emit(
                    context=ColumnUsageContext.ASSIGN_TARGET,
                    alias_used=None,
                    expression=script_text(col),
                    line=getattr(col, "StartLine", None),
                )
        # SELECT source
        src = getattr(spec, "InsertSource", None)
        if src is not None and type_name(src) == "SelectInsertSource":
            sel = getattr(src, "Select", None)
            if sel is not None:
                self._visit_select_statement_or_expr(sel)

    def _visit_select_statement_or_expr(self, node: Any) -> None:
        # `Select` on SelectInsertSource is a QueryExpression (not SelectStatement)
        t = type_name(node)
        if t == "SelectStatement":
            self._visit_select_statement(node)
        else:
            self.visit_query_expression(node)

    def _visit_update(self, upd: Any) -> None:
        spec = getattr(upd, "UpdateSpecification", upd)
        target_ref = getattr(spec, "Target", None)
        target_ident = self._named_table_ref_ident(target_ref)
        # Build scope from target + FROM clause (if any)
        scope = QueryScope()
        if target_ref is not None:
            self._collect_bindings(target_ref, scope)
        from_clause = getattr(spec, "FromClause", None)
        if from_clause is not None:
            for tref in from_clause.TableReferences:
                self._collect_bindings(tref, scope)
        self._scope_stack.append(scope)
        try:
            for sc in getattr(spec, "SetClauses", None) or []:
                if type_name(sc) != "AssignmentSetClause":
                    continue
                col = getattr(sc, "Column", None)
                if col is not None:
                    parts = multipart_parts(col.MultiPartIdentifier)
                    if parts and parts[-1].lower() == self.target_column:
                        # Attribute to target only if prefix or target-ident matches.
                        prefix = parts[-2].lower() if len(parts) >= 2 else None
                        if prefix is None:
                            # Bare col in UPDATE SET — belongs to update target.
                            if target_ident == self._target_key:
                                self._emit(
                                    context=ColumnUsageContext.ASSIGN_TARGET,
                                    alias_used=None,
                                    expression=script_text(col),
                                    line=getattr(col, "StartLine", None),
                                )
                        else:
                            binding = scope.lookup_by_alias(prefix)
                            if binding and self._binding_matches_target(binding):
                                self._emit(
                                    context=ColumnUsageContext.ASSIGN_TARGET,
                                    alias_used=prefix,
                                    expression=script_text(col),
                                    line=getattr(col, "StartLine", None),
                                )
                value = getattr(sc, "NewValue", None)
                if value is not None:
                    self._push_walk(value, ColumnUsageContext.ASSIGN_VALUE)
            self._walk_clause(
                getattr(spec, "WhereClause", None), ColumnUsageContext.WHERE
            )
        finally:
            self._scope_stack.pop()

    def _visit_merge(self, merge: Any) -> None:
        spec = getattr(merge, "MergeSpecification", merge)
        target_ref = getattr(spec, "Target", None)
        source_ref = getattr(spec, "TableReference", None)
        scope = QueryScope()
        if target_ref is not None:
            self._collect_bindings(target_ref, scope)
        if source_ref is not None:
            self._collect_bindings(source_ref, scope)
        self._scope_stack.append(scope)
        try:
            on = getattr(spec, "SearchCondition", None)
            if on is not None:
                self._push_walk(on, ColumnUsageContext.JOIN_ON)
            for action_clause in getattr(spec, "ActionClauses", None) or []:
                action = getattr(action_clause, "Action", None)
                if action is None:
                    continue
                at = type_name(action)
                if at == "UpdateMergeAction":
                    upd_spec = getattr(action, "UpdateSpecification", action)
                    for sc in getattr(upd_spec, "SetClauses", None) or []:
                        if type_name(sc) != "AssignmentSetClause":
                            continue
                        col = getattr(sc, "Column", None)
                        if col is not None:
                            self._push_walk(col, ColumnUsageContext.ASSIGN_TARGET)
                        v = getattr(sc, "NewValue", None)
                        if v is not None:
                            self._push_walk(v, ColumnUsageContext.ASSIGN_VALUE)
                elif at == "InsertMergeAction":
                    for col in getattr(action, "Columns", None) or []:
                        self._push_walk(col, ColumnUsageContext.ASSIGN_TARGET)
                    src = getattr(action, "Source", None)
                    if src is not None:
                        st = type_name(src)
                        if st == "ValuesInsertSource":
                            for row in getattr(src, "RowValues", None) or []:
                                for expr in getattr(row, "ColumnValues", None) or []:
                                    self._push_walk(expr, ColumnUsageContext.ASSIGN_VALUE)
                        elif st == "SelectInsertSource":
                            sel = getattr(src, "Select", None)
                            if sel is not None:
                                self._visit_select_statement_or_expr(sel)
        finally:
            self._scope_stack.pop()

    # -------------------------------------------------- expression walk

    def _walk_clause(self, clause: Any, context: ColumnUsageContext) -> None:
        if clause is None:
            return
        cond = getattr(clause, "SearchCondition", clause)
        self._push_walk(cond, context)

    def _walk_order_by(self, clause: Any, context: ColumnUsageContext) -> None:
        for elem in getattr(clause, "OrderByElements", []) or []:
            expr = getattr(elem, "Expression", elem)
            self._push_walk(expr, context)

    def _push_walk(self, expr: Any, context: ColumnUsageContext) -> None:
        self._context_stack.append(context)
        try:
            self._walk_expr(expr)
        finally:
            self._context_stack.pop()

    def _walk_expr(self, node: Any) -> None:
        if node is None:
            return
        t = type_name(node)

        if t == "ColumnReferenceExpression":
            self._maybe_emit_column(node)
            return
        if t == "FunctionCall":
            fname = ""
            fn = getattr(node, "FunctionName", None)
            if fn is not None:
                fname = (identifier_value(fn) or "").upper()
            is_agg = fname in _AGGREGATE_FUNCS
            if is_agg:
                self._context_stack.append(ColumnUsageContext.AGGREGATE_ARG)
            try:
                for p in getattr(node, "Parameters", []) or []:
                    self._walk_expr(p)
            finally:
                if is_agg:
                    self._context_stack.pop()
            over = getattr(node, "OverClause", None)
            if over is not None:
                for p in getattr(over, "Partitions", []) or []:
                    self._push_walk(p, ColumnUsageContext.PARTITION_BY)
                ob = getattr(over, "OrderByClause", None)
                if ob is not None:
                    self._walk_order_by(ob, ColumnUsageContext.ORDER_BY)
            return
        if t == "SearchedCaseExpression":
            for wc in getattr(node, "WhenClauses", []) or []:
                self._push_walk(
                    getattr(wc, "WhenExpression", None), ColumnUsageContext.CASE_WHEN
                )
                self._push_walk(
                    getattr(wc, "ThenExpression", None), ColumnUsageContext.CASE_THEN
                )
            self._push_walk(
                getattr(node, "ElseExpression", None), ColumnUsageContext.CASE_ELSE
            )
            return
        if t == "SimpleCaseExpression":
            self._walk_expr(getattr(node, "InputExpression", None))
            for wc in getattr(node, "WhenClauses", []) or []:
                self._push_walk(
                    getattr(wc, "WhenExpression", None), ColumnUsageContext.CASE_WHEN
                )
                self._push_walk(
                    getattr(wc, "ThenExpression", None), ColumnUsageContext.CASE_THEN
                )
            self._push_walk(
                getattr(node, "ElseExpression", None), ColumnUsageContext.CASE_ELSE
            )
            return
        if t == "BinaryExpression":
            self._walk_expr(node.FirstExpression)
            self._walk_expr(node.SecondExpression)
            return
        if t == "UnaryExpression":
            self._walk_expr(node.Expression)
            return
        if t == "ParenthesisExpression":
            self._walk_expr(node.Expression)
            return
        if t in {"CastCall", "ConvertCall", "TryCastCall", "TryConvertCall"}:
            self._walk_expr(getattr(node, "Parameter", None))
            return
        if t == "CoalesceExpression":
            for e in getattr(node, "Expressions", []) or []:
                self._walk_expr(e)
            return
        if t == "IIfCall":
            self._walk_expr(getattr(node, "Predicate", None))
            self._walk_expr(getattr(node, "ThenExpression", None))
            self._walk_expr(getattr(node, "ElseExpression", None))
            return
        if t == "BooleanBinaryExpression":
            self._walk_expr(node.FirstExpression)
            self._walk_expr(node.SecondExpression)
            return
        if t == "BooleanParenthesisExpression":
            self._walk_expr(node.Expression)
            return
        if t == "BooleanComparisonExpression":
            self._walk_expr(node.FirstExpression)
            self._walk_expr(node.SecondExpression)
            return
        if t == "BooleanTernaryExpression":
            self._walk_expr(getattr(node, "FirstExpression", None))
            self._walk_expr(getattr(node, "SecondExpression", None))
            self._walk_expr(getattr(node, "ThirdExpression", None))
            return
        if t == "BooleanIsNullExpression":
            self._walk_expr(getattr(node, "Expression", None))
            return
        if t == "InPredicate":
            self._walk_expr(getattr(node, "Expression", None))
            for v in getattr(node, "Values", []) or []:
                self._walk_expr(v)
            return
        if t == "LikePredicate":
            self._walk_expr(getattr(node, "FirstExpression", None))
            self._walk_expr(getattr(node, "SecondExpression", None))
            return
        if t == "BooleanNotExpression":
            self._walk_expr(getattr(node, "Expression", None))
            return
        # ScalarSubquery, VariableReference, Literals — nothing to emit here.

    # -------------------------------------------------- emitters

    def _maybe_emit_column(self, node: Any) -> None:
        parts = multipart_parts(getattr(node, "MultiPartIdentifier", None))
        if not parts:
            return
        col = parts[-1]
        if col.lower() != self.target_column:
            return
        prefix = parts[-2] if len(parts) >= 2 else None
        alias_used: str | None = None
        matched = False
        if prefix is not None:
            if not self._scope_stack:
                return
            binding = self._scope_stack[-1].lookup_by_alias(prefix)
            if binding and self._binding_matches_target(binding):
                matched = True
                alias_used = prefix
        else:
            # Unqualified — only accept when the current scope has exactly one
            # binding and it matches the target.
            if not self._scope_stack:
                return
            scope = self._scope_stack[-1]
            single = _single_matching_binding(scope, self._target_key)
            if single is not None:
                matched = True
                alias_used = None
            else:
                self.warnings.append(
                    "unqualified column '"
                    + col
                    + "' seen with multiple / non-matching FROM sources — "
                    "skipped to avoid false positive"
                )
                return
        if not matched:
            return
        context = (
            self._context_stack[-1]
            if self._context_stack
            else ColumnUsageContext.PROJECTION
        )
        self._emit(
            context=context,
            alias_used=alias_used,
            expression=script_text(node),
            line=getattr(node, "StartLine", None),
        )

    def _emit(
        self,
        context: ColumnUsageContext,
        alias_used: str | None,
        expression: str | None,
        line: int | None,
    ) -> None:
        self.citations.append(
            ColumnCitation(
                consumer=self.consumer,
                context=context,
                alias_used=alias_used,
                expression=expression,
                statement_kind=self._stmt_kind or "",
                line=line if isinstance(line, int) and line > 0 else None,
            )
        )

    # -------------------------------------------------- helpers

    def _binding_matches_target(self, b: TableBinding) -> bool:
        if b.source_ref is None:
            return False
        return (
            b.source_ref.schema_name.lower(),
            b.source_ref.name.lower(),
        ) == self._target_key

    def _prefix_matches_target(self, prefix: str) -> bool:
        if not self._scope_stack:
            return False
        binding = self._scope_stack[-1].lookup_by_alias(prefix)
        return binding is not None and self._binding_matches_target(binding)

    def _named_table_ref_ident(self, tref: Any) -> tuple[str, str] | None:
        if tref is None or type_name(tref) != "NamedTableReference":
            return None
        _db, schema, name = schema_object_parts(tref.SchemaObject)
        if not name:
            return None
        return (schema.lower() if schema else "dbo", name.lower())


def _single_matching_binding(
    scope: QueryScope, target_key: tuple[str, str]
) -> TableBinding | None:
    bindings = list(scope.bindings.values())
    if len(bindings) != 1:
        return None
    b = bindings[0]
    if b.source_ref is None:
        return None
    if (
        b.source_ref.schema_name.lower(),
        b.source_ref.name.lower(),
    ) == target_key:
        return b
    return None


def b_stmts(batch: Any):  # noqa: ANN201
    return batch.Statements
