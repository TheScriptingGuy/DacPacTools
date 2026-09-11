from __future__ import annotations

from typing import Any

from dacpactools.application.ports._handles import ColumnResolver, ParsedBatch, ParsedLineage
from dacpactools.domain.lineage import ColumnMapping, ColumnUsage, LineageNode
from dacpactools.infrastructure.scriptdom.ast_utils import type_name
from dacpactools.infrastructure.scriptdom.ctas_extractor import CtasSpec, extract_ctas
from dacpactools.infrastructure.scriptdom.visitors.select_lineage_visitor import (
    SelectLineageVisitor,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("scriptdom_parser")


def _clr_types() -> tuple[Any, Any]:
    from Microsoft.SqlServer.TransactSql.ScriptDom import TSql160Parser  # type: ignore
    from System.IO import StringReader  # type: ignore

    return TSql160Parser, StringReader


class ScriptDomParserService:
    def __init__(self, initial_quoted_identifiers: bool = True) -> None:
        self._initial_qi = initial_quoted_identifiers

    def parse(self, tsql: str) -> ParsedBatch:
        TSql160Parser, StringReader = _clr_types()
        parser = TSql160Parser(self._initial_qi)
        reader = StringReader(tsql)
        try:
            fragment, errors = parser.Parse(reader, None)
        finally:
            reader.Dispose()
        error_msgs: tuple[str, ...] = ()
        if errors is not None and errors.Count > 0:
            error_msgs = tuple(f"line {e.Line} col {e.Column}: {e.Message}" for e in errors)
        return ParsedBatch(fragment=fragment, errors=error_msgs)

    def extract_column_mappings(
        self,
        parsed: ParsedBatch,
        node: LineageNode,
        resolver: ColumnResolver,
    ) -> tuple[ColumnMapping, ...]:
        return self.extract_lineage(parsed, node, resolver).mappings  # type: ignore[return-value]

    def extract_lineage(
        self,
        parsed: ParsedBatch,
        node: LineageNode,
        resolver: ColumnResolver,
    ) -> ParsedLineage:
        if parsed.fragment is None:
            return ParsedLineage(mappings=(), predicates=(), ctas_specs=())
        visitor = SelectLineageVisitor(node=node, resolver=resolver)
        all_mappings: list[ColumnMapping] = []
        for stmt in self._lineage_producing_statements(parsed.fragment):
            t = type_name(stmt)
            if t == "SelectStatement":
                all_mappings.extend(visitor.visit_select_statement(stmt))
            elif t == "InsertStatement":
                all_mappings.extend(visitor.visit_insert_statement(stmt.InsertSpecification))
            elif t == "MergeStatement":
                all_mappings.extend(visitor.visit_merge_statement(stmt))
        ctas_specs: list[CtasSpec] = []
        for ct in self._ctas_statements(parsed.fragment):
            spec = extract_ctas(ct)
            if spec is not None:
                ctas_specs.append(spec)
        for w in visitor.warnings:
            log.debug("parser warning", node=node.ref.qualified(), msg=w)
        preds: tuple[ColumnUsage, ...] = tuple(visitor.predicates)
        return ParsedLineage(
            mappings=tuple(all_mappings),
            predicates=preds,
            ctas_specs=tuple(ctas_specs),
        )

    def _ctas_statements(self, fragment: Any) -> list[Any]:
        out: list[Any] = []
        for batch in getattr(fragment, "Batches", []) or []:
            for stmt in batch.Statements:
                t = type_name(stmt)
                if t == "CreateTableStatement":
                    out.append(stmt)
                elif t in {
                    "CreateProcedureStatement",
                    "CreateOrAlterProcedureStatement",
                    "AlterProcedureStatement",
                    "CreateFunctionStatement",
                    "CreateOrAlterFunctionStatement",
                    "AlterFunctionStatement",
                }:
                    out.extend(self._ctas_in_body(getattr(stmt, "StatementList", None)))
        return out

    def _ctas_in_body(self, statement_list: Any) -> list[Any]:
        out: list[Any] = []
        if statement_list is None:
            return out
        for stmt in statement_list.Statements:
            t = type_name(stmt)
            if t == "CreateTableStatement":
                out.append(stmt)
            elif t == "BeginEndBlockStatement":
                out.extend(self._ctas_in_body(stmt.StatementList))
            elif t in {"IfStatement", "WhileStatement"}:
                for attr in ("ThenStatement", "Statement", "ElseStatement"):
                    inner = getattr(stmt, attr, None)
                    if inner is None:
                        continue
                    it = type_name(inner)
                    if it == "BeginEndBlockStatement":
                        out.extend(self._ctas_in_body(inner.StatementList))
                    elif it == "CreateTableStatement":
                        out.append(inner)
        return out

    def _lineage_producing_statements(self, fragment: Any) -> list[Any]:
        """Walk a TSqlScript / CreateView / CreateProcedure and yield any statement that
        produces column-level lineage: SELECT, INSERT..., MERGE."""
        out: list[Any] = []
        for batch in getattr(fragment, "Batches", []) or []:
            for stmt in batch.Statements:
                t = type_name(stmt)
                if t in {"CreateViewStatement", "CreateOrAlterViewStatement", "AlterViewStatement"}:
                    out.append(stmt.SelectStatement)
                elif t in {
                    "CreateProcedureStatement",
                    "CreateOrAlterProcedureStatement",
                    "AlterProcedureStatement",
                }:
                    out.extend(self._body_producers(stmt.StatementList))
                elif t in {
                    "CreateFunctionStatement",
                    "CreateOrAlterFunctionStatement",
                    "AlterFunctionStatement",
                }:
                    body = getattr(stmt, "StatementList", None)
                    if body is not None:
                        out.extend(self._body_producers(body))
                    inline = getattr(stmt, "ReturnType", None)
                    if inline is not None:
                        select = getattr(inline, "SelectStatement", None)
                        if select is not None:
                            out.append(select)
                elif t in {"SelectStatement", "InsertStatement", "MergeStatement"}:
                    out.append(stmt)
        return out

    def _body_producers(self, statement_list: Any) -> list[Any]:
        out: list[Any] = []
        if statement_list is None:
            return out
        for stmt in statement_list.Statements:
            out.extend(self._body_producers_of_single(stmt))
        return out

    def _body_producers_of_single(self, stmt: Any) -> list[Any]:
        t = type_name(stmt)
        if t == "BeginEndBlockStatement":
            return self._body_producers(stmt.StatementList)
        if t in {"SelectStatement", "InsertStatement", "MergeStatement"}:
            return [stmt]
        if t == "CreateTableStatement":
            select = getattr(stmt, "SelectStatement", None)
            return [select] if select is not None else []
        if t in {"IfStatement", "WhileStatement"}:
            out: list[Any] = []
            inner = getattr(stmt, "ThenStatement", None) or getattr(stmt, "Statement", None)
            if inner is not None:
                out.extend(self._body_producers_of_single(inner))
            else_stmt = getattr(stmt, "ElseStatement", None)
            if else_stmt is not None:
                out.extend(self._body_producers_of_single(else_stmt))
            return out
        return []

    def _select_root_of_statement(self, statement: Any) -> Any | None:
        if statement is None:
            return None
        t = type_name(statement)
        if t == "SelectStatement":
            return statement.QueryExpression
        return None
