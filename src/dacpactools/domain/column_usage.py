from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

from dacpactools.domain.identifiers import ColumnRef, ObjectRef


class ColumnUsageContext(str, Enum):
    PROJECTION = "PROJECTION"
    JOIN_ON = "JOIN_ON"
    WHERE = "WHERE"
    HAVING = "HAVING"
    GROUP_BY = "GROUP_BY"
    ORDER_BY = "ORDER_BY"
    PARTITION_BY = "PARTITION_BY"
    AGGREGATE_ARG = "AGGREGATE_ARG"
    CASE_WHEN = "CASE_WHEN"
    CASE_THEN = "CASE_THEN"
    CASE_ELSE = "CASE_ELSE"
    ASSIGN_TARGET = "ASSIGN_TARGET"
    ASSIGN_VALUE = "ASSIGN_VALUE"


class ColumnCitation(BaseModel):
    model_config = ConfigDict(frozen=True)

    consumer: ObjectRef
    context: ColumnUsageContext
    alias_used: str | None = None
    expression: str | None = None
    statement_kind: str = ""
    line: int | None = None


class ColumnUsageReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_column: ColumnRef
    citations: tuple[ColumnCitation, ...] = ()
    warnings: tuple[str, ...] = ()
