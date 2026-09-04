from __future__ import annotations

from typing import Any

from dacpactools.application.ports._handles import ColumnResolver
from dacpactools.domain.enums import ColumnUsageKind
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.lineage import ColumnMapping, ColumnUsage, LineageNode
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


class SelectLineageVisitor:
    """Extracts ColumnMapping tuples from SELECT-shaped queries.

    Scope handled: QuerySpecification, BinaryQueryExpression (UNION/EXCEPT/INTERSECT
    ordinal merge), SelectScalarExpression, SelectStarExpression (with `t.*` prefix),
    ColumnReferenceExpression, NamedTableReference (+ alias), QualifiedJoin,
    QueryDerivedTable (subquery in FROM), CommonTableExpression (WITH).
    INSERT/MERGE/computed-fn expressions live in v3 visitors.
    """

    def __init__(self, node: LineageNode, resolver: ColumnResolver) -> None:
        self.node = node
        self.resolver = resolver
        self.warnings: list[str] = []
        self.predicates: list[ColumnUsage] = []
        self._cte_expansions: dict[str, tuple[tuple[ColumnRef, ...], ...]] = {}

    # ------------------------------------------------------------------ public

    def visit_select_statement(self, select_stmt: Any) -> list[ColumnMapping]:
        """Entry point when the parser hands us a SelectStatement (has WITH CTEs)."""
        cte_scope = QueryScope()
        with_clause = getattr(select_stmt, "WithCtesAndXmlNamespaces", None)
        if with_clause is not None:
            for cte in getattr(with_clause, "CommonTableExpressions", []) or []:
                self._register_cte(cte, cte_scope)
        mappings = self.visit_query_expression(select_stmt.QueryExpression, cte_scope)
        self._collect_order_by(getattr(select_stmt, "OrderByClause", None), cte_scope)
        return mappings

    def visit_insert_statement(self, insert_spec: Any) -> list[ColumnMapping]:
        """`INSERT INTO T (c1,c2) SELECT a,b FROM S` -> maps by ordinal to T's cols."""
        target_ref, target_cols = self._resolve_insert_target(insert_spec)
        source = getattr(insert_spec, "InsertSource", None)
        if source is None:
            return []
        st = type_name(source)
        if st != "SelectInsertSource":
            self.warnings.append(f"insert source not SELECT-based: {st}")
            return []
        select = getattr(source, "Select", None)
        if select is None:
            return []
        select_mappings = self.visit_query_expression(select)
        return self._realign_to_target(select_mappings, target_ref, target_cols, insert_spec)

    def visit_merge_statement(self, merge_stmt: Any) -> list[ColumnMapping]:
        merge_spec = getattr(merge_stmt, "MergeSpecification", merge_stmt)
        target_table_ref = getattr(merge_spec, "Target", None)
        if target_table_ref is None:
            return []
        target_ref, target_alias, target_cols = self._resolve_named_table(target_table_ref)

        scope = QueryScope()
        scope.add(TableBinding(alias=target_alias, source_ref=target_ref, columns=target_cols))
        using = getattr(merge_spec, "TableReference", None)
        if using is not None:
            self._collect_bindings(using, scope)

        mappings: list[ColumnMapping] = []
        for action in getattr(merge_spec, "ActionClauses", []) or []:
            action_body = getattr(action, "Action", None)
            if action_body is None:
                continue
            at = type_name(action_body)
            if at == "UpdateMergeAction":
                mappings.extend(self._update_set_mappings(action_body, target_ref, target_cols, scope))
            elif at == "InsertMergeAction":
                mappings.extend(self._insert_merge_mappings(action_body, target_ref, target_cols, scope))
        return mappings

    def _resolve_insert_target(
        self, insert_spec: Any
    ) -> tuple[ObjectRef | None, tuple[ColumnRef, ...]]:
        target_ref_node = getattr(insert_spec, "Target", None)
        if target_ref_node is None:
            return None, ()
        target_ref, _, base_cols = self._resolve_named_table(target_ref_node)
        # Explicit column list: INSERT INTO T (c1, c2)
        col_list = list(getattr(insert_spec, "Columns", []) or [])
        if col_list:
            resolved: list[ColumnRef] = []
            for i, c in enumerate(col_list):
                parts = multipart_parts(c.MultiPartIdentifier)
                name = parts[-1] if parts else f"col{i}"
                match = next((x for x in base_cols if x.column_name.lower() == name.lower()), None)
                if match is not None:
                    resolved.append(
                        ColumnRef(
                            owner=target_ref or match.owner,
                            column_name=match.column_name,
                            ordinal=i,
                            data_type=match.data_type,
                        )
                    )
                else:
                    resolved.append(
                        ColumnRef(owner=target_ref or self.node.ref, column_name=name, ordinal=i)
                    )
            return target_ref, tuple(resolved)
        return target_ref, base_cols

    def _resolve_named_table(
        self, table_ref: Any
    ) -> tuple[ObjectRef | None, str, tuple[ColumnRef, ...]]:
        t = type_name(table_ref)
        if t != "NamedTableReference":
            return None, "", ()
        db, schema, name = schema_object_parts(table_ref.SchemaObject)
        alias_ident = table_ref.Alias
        alias = identifier_value(alias_ident) or (name or "")
        ref = ObjectRef(database=db, schema=schema or "dbo", name=name or "?")
        cols = self._lookup_source_columns(schema, name)
        return ref, alias, cols

    def _realign_to_target(
        self,
        select_mappings: list[ColumnMapping],
        target_ref: ObjectRef | None,
        target_cols: tuple[ColumnRef, ...],
        insert_spec: Any,
    ) -> list[ColumnMapping]:
        if target_ref is None or not target_cols:
            return select_mappings
        out: list[ColumnMapping] = []
        for i, sel in enumerate(select_mappings):
            if i >= len(target_cols):
                self.warnings.append(f"INSERT: too many SELECT columns at ordinal {i}")
                break
            tgt = target_cols[i]
            realigned_target = ColumnRef(
                owner=target_ref,
                column_name=tgt.column_name,
                ordinal=i,
                data_type=tgt.data_type,
            )
            out.append(
                ColumnMapping(
                    target=realigned_target,
                    sources=sel.sources,
                    expression=sel.expression,
                    is_direct=sel.is_direct,
                )
            )
        return out

    def _update_set_mappings(
        self,
        update_action: Any,
        target_ref: ObjectRef | None,
        target_cols: tuple[ColumnRef, ...],
        scope: QueryScope,
    ) -> list[ColumnMapping]:
        source_stmt = getattr(update_action, "UpdateSpecification", update_action)
        set_clauses = getattr(source_stmt, "SetClauses", None) or []
        out: list[ColumnMapping] = []
        for sc in set_clauses:
            if type_name(sc) != "AssignmentSetClause":
                continue
            col_ref = getattr(sc, "Column", None)
            if col_ref is None:
                continue
            parts = multipart_parts(col_ref.MultiPartIdentifier)
            col_name = parts[-1] if parts else ""
            tgt_col = next((c for c in target_cols if c.column_name.lower() == col_name.lower()), None)
            realigned = ColumnRef(
                owner=target_ref or self.node.ref,
                column_name=col_name,
                ordinal=tgt_col.ordinal if tgt_col else None,
                data_type=tgt_col.data_type if tgt_col else None,
            )
            expr = getattr(sc, "NewValue", None)
            sources, is_direct = self._collect_sources(expr, scope)
            out.append(
                ColumnMapping(
                    target=realigned,
                    sources=tuple(sources),
                    expression=script_text(expr) or "",
                    is_direct=is_direct,
                )
            )
        return out

    def _insert_merge_mappings(
        self,
        insert_action: Any,
        target_ref: ObjectRef | None,
        target_cols: tuple[ColumnRef, ...],
        scope: QueryScope,
    ) -> list[ColumnMapping]:
        column_list = list(getattr(insert_action, "Columns", []) or [])
        source = getattr(insert_action, "Source", None)
        if source is None:
            return []
        values: list[Any] = []
        st = type_name(source)
        if st == "ValuesInsertSource":
            rows = list(getattr(source, "RowValues", []) or [])
            if rows:
                values = list(rows[0].ColumnValues)
        elif st == "SelectInsertSource":
            select_mappings = self.visit_query_expression(source.Select)
            return self._realign_to_target(select_mappings, target_ref, target_cols, insert_action)

        out: list[ColumnMapping] = []
        for i, expr in enumerate(values):
            if i < len(column_list):
                parts = multipart_parts(column_list[i].MultiPartIdentifier)
                col_name = parts[-1] if parts else f"col{i}"
            elif i < len(target_cols):
                col_name = target_cols[i].column_name
            else:
                col_name = f"col{i}"
            tgt_col = next((c for c in target_cols if c.column_name.lower() == col_name.lower()), None)
            realigned = ColumnRef(
                owner=target_ref or self.node.ref,
                column_name=col_name,
                ordinal=i,
                data_type=tgt_col.data_type if tgt_col else None,
            )
            sources, is_direct = self._collect_sources(expr, scope)
            out.append(
                ColumnMapping(
                    target=realigned,
                    sources=tuple(sources),
                    expression=script_text(expr) or "",
                    is_direct=is_direct,
                )
            )
        return out

    def visit_query_expression(
        self, query_expression: Any, parent_scope: QueryScope | None = None
    ) -> list[ColumnMapping]:
        t = type_name(query_expression)
        if t == "QuerySpecification":
            return self._visit_query_spec(query_expression, parent_scope)
        if t == "BinaryQueryExpression":
            left = self.visit_query_expression(query_expression.FirstQueryExpression, parent_scope)
            right = self.visit_query_expression(query_expression.SecondQueryExpression, parent_scope)
            return self._merge_by_ordinal(left, right)
        if t == "QueryParenthesisExpression":
            return self.visit_query_expression(query_expression.QueryExpression, parent_scope)
        self.warnings.append(f"unsupported query expression: {t}")
        return []

    def _register_cte(self, cte: Any, cte_scope: QueryScope) -> None:
        cte_name = identifier_value(cte.ExpressionName) or ""
        inner_mappings = self.visit_query_expression(cte.QueryExpression, cte_scope)
        explicit_cols = list(getattr(cte, "Columns", []) or [])
        virtual_ref = ObjectRef(
            database=None, schema="__cte__", name=cte_name
        )
        virtual_cols: list[ColumnRef] = []
        for i, m in enumerate(inner_mappings):
            if i < len(explicit_cols):
                col_name = identifier_value(explicit_cols[i].Value) or m.target.column_name
            else:
                col_name = m.target.column_name
            virtual_cols.append(
                ColumnRef(owner=virtual_ref, column_name=col_name, ordinal=i)
            )
        cte_scope.add(
            TableBinding(alias=cte_name, source_ref=virtual_ref, columns=tuple(virtual_cols))
        )
        # Track which base columns each CTE column ultimately comes from — used when a
        # downstream SELECT reads from the CTE.
        self._cte_expansions[cte_name.lower()] = tuple(
            tuple(m.sources) for m in inner_mappings
        )

    # --------------------------------------------------------------- internal

    def _visit_query_spec(
        self, qs: Any, parent_scope: QueryScope | None
    ) -> list[ColumnMapping]:
        scope = QueryScope(parent=parent_scope)
        if qs.FromClause is not None:
            for table_ref in qs.FromClause.TableReferences:
                self._collect_bindings(table_ref, scope)

        mappings: list[ColumnMapping] = []
        ordinal = 0
        target_columns = self.node.columns
        for elem in qs.SelectElements:
            for m in self._map_select_element(elem, scope, ordinal, target_columns):
                mappings.append(m)
                ordinal += 1

        self._collect_predicate(getattr(qs, "WhereClause", None), scope, ColumnUsageKind.WHERE)
        self._collect_group_by(getattr(qs, "GroupByClause", None), scope)
        self._collect_predicate(getattr(qs, "HavingClause", None), scope, ColumnUsageKind.HAVING)
        return mappings

    def _collect_bindings(self, table_ref: Any, scope: QueryScope) -> None:
        t = type_name(table_ref)
        if t == "NamedTableReference":
            db, schema, name = schema_object_parts(table_ref.SchemaObject)
            alias_ident = table_ref.Alias
            alias = identifier_value(alias_ident) or name or ""
            columns = self._lookup_source_columns(schema, name)
            source_ref = None
            if name:
                source_ref = ObjectRef(
                    database=db, schema=schema or "dbo", name=name
                )
            scope.add(TableBinding(alias=alias, source_ref=source_ref, columns=columns))
            return
        if t == "QualifiedJoin":
            self._collect_bindings(table_ref.FirstTableReference, scope)
            self._collect_bindings(table_ref.SecondTableReference, scope)
            self._collect_join_condition(getattr(table_ref, "SearchCondition", None), scope)
            return
        if t == "UnqualifiedJoin":
            self._collect_bindings(table_ref.FirstTableReference, scope)
            self._collect_bindings(table_ref.SecondTableReference, scope)
            return
        if t == "QueryDerivedTable":
            alias_ident = table_ref.Alias
            alias = identifier_value(alias_ident) or ""
            inner_mappings = self.visit_query_expression(table_ref.QueryExpression, scope)
            virtual_ref = ObjectRef(database=None, schema="__derived__", name=alias or "_")
            virtual_cols = tuple(
                ColumnRef(owner=virtual_ref, column_name=m.target.column_name, ordinal=i)
                for i, m in enumerate(inner_mappings)
            )
            scope.add(TableBinding(alias=alias, source_ref=virtual_ref, columns=virtual_cols))
            # Remember base-column expansions so column refs against the derived table
            # can be traced through.
            self._cte_expansions[f"__derived__:{alias.lower()}"] = tuple(
                tuple(m.sources) for m in inner_mappings
            )
            return
        self.warnings.append(f"unsupported table reference: {t}")

    def _map_select_element(
        self,
        elem: Any,
        scope: QueryScope,
        base_ordinal: int,
        target_columns: tuple[ColumnRef, ...],
    ) -> list[ColumnMapping]:
        t = type_name(elem)
        if t == "SelectScalarExpression":
            return [self._map_scalar(elem, scope, base_ordinal, target_columns)]
        if t == "SelectStarExpression":
            return self._expand_star(elem, scope, base_ordinal, target_columns)
        if t == "SelectSetVariable":
            return []  # variable assignment; not a projected column
        self.warnings.append(f"unsupported select element: {t}")
        return []

    def _map_scalar(
        self,
        elem: Any,
        scope: QueryScope,
        ordinal: int,
        target_columns: tuple[ColumnRef, ...],
    ) -> ColumnMapping:
        expr = elem.Expression
        expression_text = script_text(expr) or ""
        alias = identifier_value(elem.ColumnName.Identifier) if elem.ColumnName is not None else None
        target_col = self._target_column(alias, expr, ordinal, target_columns)
        sources, is_direct = self._collect_sources(expr, scope)
        return ColumnMapping(
            target=target_col,
            sources=tuple(sources),
            expression=expression_text,
            is_direct=is_direct,
        )

    def _expand_star(
        self,
        elem: Any,
        scope: QueryScope,
        base_ordinal: int,
        target_columns: tuple[ColumnRef, ...],
    ) -> list[ColumnMapping]:
        # Optional t.* prefix
        qualifier: str | None = None
        qual = getattr(elem, "Qualifier", None)
        if qual is not None:
            parts = multipart_parts(qual)
            qualifier = parts[-1] if parts else None
        expanded = scope.all_columns_expanded(qualifier)
        out: list[ColumnMapping] = []
        for i, src in enumerate(expanded):
            ordinal = base_ordinal + i
            target = self._target_column(src.column_name, None, ordinal, target_columns)
            out.append(
                ColumnMapping(
                    target=target,
                    sources=(src,),
                    expression=f"{qualifier + '.' if qualifier else ''}*",
                    is_direct=True,
                )
            )
        return out

    def _target_column(
        self,
        alias: str | None,
        expr: Any | None,
        ordinal: int,
        target_columns: tuple[ColumnRef, ...],
    ) -> ColumnRef:
        # Prefer the declared table column at this ordinal (from DacFx metadata).
        if ordinal < len(target_columns):
            base = target_columns[ordinal]
            name = alias or base.column_name
            return ColumnRef(owner=self.node.ref, column_name=name, ordinal=ordinal, data_type=base.data_type)
        # Fallback: infer name.
        name = alias or self._infer_bare_name(expr) or f"col{ordinal}"
        return ColumnRef(owner=self.node.ref, column_name=name, ordinal=ordinal)

    def _infer_bare_name(self, expr: Any) -> str | None:
        if expr is None:
            return None
        if type_name(expr) == "ColumnReferenceExpression":
            parts = multipart_parts(expr.MultiPartIdentifier)
            if parts:
                return parts[-1]
        return None

    def _collect_sources(
        self, expr: Any, scope: QueryScope
    ) -> tuple[list[ColumnRef], bool]:
        """Return (source columns, is_direct). Recurses through the expression tree."""
        sources: list[ColumnRef] = []
        is_direct_container = {"value": True}
        self._walk_expression(expr, scope, sources, is_direct_container, depth=0)
        return sources, is_direct_container["value"]

    def _walk_expression(
        self,
        expr: Any,
        scope: QueryScope,
        sinks: list[ColumnRef],
        is_direct: dict[str, bool],
        depth: int,
    ) -> None:
        if expr is None:
            return
        t = type_name(expr)

        if t == "ColumnReferenceExpression":
            parts = multipart_parts(expr.MultiPartIdentifier)
            if not parts:
                return
            col = parts[-1]
            prefix = parts[-2] if len(parts) >= 2 else None
            matches = scope.resolve_column(prefix, col)
            if not matches:
                self.warnings.append(f"unresolved column: {'.'.join(parts)}")
            expanded: list[ColumnRef] = []
            for m in matches:
                if m.owner.schema_name in ("__cte__", "__derived__"):
                    expanded.extend(self._expand_virtual_column(m))
                else:
                    expanded.append(m)
            for m in expanded:
                if m not in sinks:
                    sinks.append(m)
            if depth > 0:
                is_direct["value"] = False
            return

        if t == "FunctionCall":
            is_direct["value"] = False
            for arg in getattr(expr, "Parameters", []) or []:
                self._walk_expression(arg, scope, sinks, is_direct, depth + 1)
            over = getattr(expr, "OverClause", None)
            if over is not None:
                self._collect_over_clause(over, scope)
            return

        if t == "SearchedCaseExpression":
            is_direct["value"] = False
            for wc in getattr(expr, "WhenClauses", []) or []:
                self._walk_expression(getattr(wc, "WhenExpression", None), scope, sinks, is_direct, depth + 1)
                self._walk_expression(getattr(wc, "ThenExpression", None), scope, sinks, is_direct, depth + 1)
            self._walk_expression(getattr(expr, "ElseExpression", None), scope, sinks, is_direct, depth + 1)
            return

        if t == "SimpleCaseExpression":
            is_direct["value"] = False
            self._walk_expression(expr.InputExpression, scope, sinks, is_direct, depth + 1)
            for wc in getattr(expr, "WhenClauses", []) or []:
                self._walk_expression(getattr(wc, "WhenExpression", None), scope, sinks, is_direct, depth + 1)
                self._walk_expression(getattr(wc, "ThenExpression", None), scope, sinks, is_direct, depth + 1)
            self._walk_expression(getattr(expr, "ElseExpression", None), scope, sinks, is_direct, depth + 1)
            return

        if t == "BinaryExpression":
            is_direct["value"] = False
            self._walk_expression(expr.FirstExpression, scope, sinks, is_direct, depth + 1)
            self._walk_expression(expr.SecondExpression, scope, sinks, is_direct, depth + 1)
            return

        if t == "UnaryExpression":
            self._walk_expression(expr.Expression, scope, sinks, is_direct, depth + 1)
            return

        if t == "ParenthesisExpression":
            self._walk_expression(expr.Expression, scope, sinks, is_direct, depth)
            return

        if t == "CastCall" or t == "ConvertCall" or t == "TryCastCall" or t == "TryConvertCall":
            is_direct["value"] = False
            self._walk_expression(expr.Parameter, scope, sinks, is_direct, depth + 1)
            return

        if t == "CoalesceExpression":
            is_direct["value"] = False
            for e in expr.Expressions:
                self._walk_expression(e, scope, sinks, is_direct, depth + 1)
            return

        if t == "ScalarSubquery":
            # subquery inside SELECT list — v2/v3 feature
            self.warnings.append("scalar subquery not resolved (v2 feature)")
            is_direct["value"] = False
            return

        # Literals / NullLiteral / IntegerLiteral / VariableReference / etc — no sources
        if t.endswith("Literal") or t == "VariableReference":
            is_direct["value"] = False
            return

    def _expand_virtual_column(self, col: ColumnRef) -> tuple[ColumnRef, ...]:
        """CTE / derived-table column -> the base columns it was traced to."""
        owner = col.owner
        if owner.schema_name == "__cte__":
            key = owner.name.lower()
        elif owner.schema_name == "__derived__":
            key = f"__derived__:{owner.name.lower()}"
        else:
            return (col,)
        expansions = self._cte_expansions.get(key)
        if expansions is None or col.ordinal is None or col.ordinal >= len(expansions):
            return (col,)
        base_sources = expansions[col.ordinal]
        out: list[ColumnRef] = []
        for src in base_sources:
            if src.owner.schema_name in ("__cte__", "__derived__"):
                out.extend(self._expand_virtual_column(src))
            else:
                out.append(src)
        return tuple(out) if out else (col,)

    # -------------------------------------------------------- helpers

    def _lookup_source_columns(self, schema: str | None, name: str | None) -> tuple[ColumnRef, ...]:
        if not name:
            return ()
        keys = []
        if schema:
            keys.append(f"{schema.lower()}.{name.lower()}")
        keys.append(name.lower())
        for k in keys:
            cols = self.resolver.scope.get(k)
            if cols:
                return tuple(cols)
        return ()

    # -------------------------------------------------------- predicates

    def _collect_predicate(
        self, clause: Any, scope: QueryScope, kind: ColumnUsageKind
    ) -> None:
        if clause is None:
            return
        cond = getattr(clause, "SearchCondition", clause)
        cols = self._columns_from_expr(cond, scope)
        self.predicates.append(
            ColumnUsage(
                kind=kind,
                columns=tuple(cols),
                expression=script_text(cond) or None,
                partner=None,
            )
        )

    def _collect_join_condition(self, cond: Any, scope: QueryScope) -> None:
        if cond is None:
            return
        # Emit one ColumnUsage per equality pair; fallback to whole-expr for non-eq.
        pairs = self._flatten_equality_pairs(cond, scope)
        if pairs:
            for left_cols, right_cols, pair_text in pairs:
                # pair form: attach left as columns, right as partner (first each).
                left = left_cols[0] if left_cols else None
                right = right_cols[0] if right_cols else None
                cols = tuple(left_cols) + tuple(c for c in right_cols if c not in left_cols)
                self.predicates.append(
                    ColumnUsage(
                        kind=ColumnUsageKind.JOIN_ON,
                        columns=cols,
                        expression=pair_text,
                        partner=right if left is not None else None,
                    )
                )
            return
        cols = self._columns_from_expr(cond, scope)
        self.predicates.append(
            ColumnUsage(
                kind=ColumnUsageKind.JOIN_ON,
                columns=tuple(cols),
                expression=script_text(cond) or None,
                partner=None,
            )
        )

    def _collect_group_by(self, clause: Any, scope: QueryScope) -> None:
        if clause is None:
            return
        specs = getattr(clause, "GroupingSpecifications", []) or []
        cols: list[ColumnRef] = []
        for spec in specs:
            expr = getattr(spec, "Expression", spec)
            for c in self._columns_from_expr(expr, scope):
                if c not in cols:
                    cols.append(c)
        if cols:
            self.predicates.append(
                ColumnUsage(
                    kind=ColumnUsageKind.GROUP_BY,
                    columns=tuple(cols),
                    expression=script_text(clause) or None,
                )
            )

    def _collect_order_by(self, clause: Any, scope: QueryScope) -> None:
        if clause is None:
            return
        elements = getattr(clause, "OrderByElements", []) or []
        cols: list[ColumnRef] = []
        for elem in elements:
            expr = getattr(elem, "Expression", elem)
            for c in self._columns_from_expr(expr, scope):
                if c not in cols:
                    cols.append(c)
        if cols:
            self.predicates.append(
                ColumnUsage(
                    kind=ColumnUsageKind.ORDER_BY,
                    columns=tuple(cols),
                    expression=script_text(clause) or None,
                )
            )

    def _collect_over_clause(self, over: Any, scope: QueryScope) -> None:
        partitions = list(getattr(over, "Partitions", []) or [])
        if partitions:
            cols: list[ColumnRef] = []
            for p in partitions:
                for c in self._columns_from_expr(p, scope):
                    if c not in cols:
                        cols.append(c)
            if cols:
                self.predicates.append(
                    ColumnUsage(
                        kind=ColumnUsageKind.PARTITION_BY,
                        columns=tuple(cols),
                        expression=script_text(over) or None,
                    )
                )
        order_by = getattr(over, "OrderByClause", None)
        if order_by is not None:
            elements = getattr(order_by, "OrderByElements", []) or []
            cols2: list[ColumnRef] = []
            for elem in elements:
                expr = getattr(elem, "Expression", elem)
                for c in self._columns_from_expr(expr, scope):
                    if c not in cols2:
                        cols2.append(c)
            if cols2:
                self.predicates.append(
                    ColumnUsage(
                        kind=ColumnUsageKind.ORDER_BY,
                        columns=tuple(cols2),
                        expression=script_text(order_by) or None,
                    )
                )

    def _columns_from_expr(self, expr: Any, scope: QueryScope) -> list[ColumnRef]:
        sinks: list[ColumnRef] = []
        self._walk_expression(expr, scope, sinks, {"value": True}, depth=0)
        return sinks

    def _flatten_equality_pairs(
        self, cond: Any, scope: QueryScope
    ) -> list[tuple[list[ColumnRef], list[ColumnRef], str]]:
        """Extract `col = col` pairs (AND-joined) from a JOIN search condition."""
        out: list[tuple[list[ColumnRef], list[ColumnRef], str]] = []
        self._walk_join_cond(cond, scope, out)
        return out

    def _walk_join_cond(
        self,
        node: Any,
        scope: QueryScope,
        out: list[tuple[list[ColumnRef], list[ColumnRef], str]],
    ) -> None:
        if node is None:
            return
        t = type_name(node)
        if t == "BooleanBinaryExpression":
            # AND / OR — only descend on AND to keep pair semantics.
            op = getattr(node, "BinaryExpressionType", None)
            op_name = str(op) if op is not None else ""
            if "And" in op_name:
                self._walk_join_cond(node.FirstExpression, scope, out)
                self._walk_join_cond(node.SecondExpression, scope, out)
            return
        if t == "BooleanParenthesisExpression":
            self._walk_join_cond(node.Expression, scope, out)
            return
        if t == "BooleanComparisonExpression":
            op = getattr(node, "ComparisonType", None)
            op_name = str(op) if op is not None else ""
            if "Equals" in op_name:
                left = self._columns_from_expr(node.FirstExpression, scope)
                right = self._columns_from_expr(node.SecondExpression, scope)
                if left and right:
                    out.append((left, right, script_text(node) or ""))
            return
        # Other predicate shapes (LIKE, IN, IS NULL, ...): skip pair extraction.
        return

    def _merge_by_ordinal(
        self, left: list[ColumnMapping], right: list[ColumnMapping]
    ) -> list[ColumnMapping]:
        n = max(len(left), len(right))
        out: list[ColumnMapping] = []
        for i in range(n):
            l = left[i] if i < len(left) else None
            r = right[i] if i < len(right) else None
            if l is None and r is None:
                continue
            target = (l or r).target  # type: ignore[union-attr]
            sources: list[ColumnRef] = []
            for m in (l, r):
                if m is None:
                    continue
                for s in m.sources:
                    if s not in sources:
                        sources.append(s)
            out.append(ColumnMapping(target=target, sources=tuple(sources), expression="UNION", is_direct=False))
        return out
