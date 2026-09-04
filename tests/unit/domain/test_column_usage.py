from __future__ import annotations

from dacpactools.domain.column_usage import (
    ColumnCitation,
    ColumnUsageContext,
    ColumnUsageReport,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


def _owner(name: str) -> ObjectRef:
    return ObjectRef(database=None, schema="SL_DMS", name=name)


def test_context_enum_covers_all_documented_kinds() -> None:
    expected = {
        "PROJECTION",
        "JOIN_ON",
        "WHERE",
        "HAVING",
        "GROUP_BY",
        "ORDER_BY",
        "PARTITION_BY",
        "AGGREGATE_ARG",
        "CASE_WHEN",
        "CASE_THEN",
        "CASE_ELSE",
        "ASSIGN_TARGET",
        "ASSIGN_VALUE",
    }
    assert {c.value for c in ColumnUsageContext} == expected


def test_citation_and_report_shape() -> None:
    owner = _owner("tdv_ADDRESSR")
    consumer = ObjectRef(database=None, schema="DA_DLV", name="LoadR2023")
    citation = ColumnCitation(
        consumer=consumer,
        context=ColumnUsageContext.JOIN_ON,
        alias_used="adr",
        expression="adr.PartyRoleID",
        statement_kind="PROCEDURE",
        line=283,
    )
    target = ColumnRef(owner=owner, column_name="PartyRoleID")
    report = ColumnUsageReport(target_column=target, citations=(citation,))
    assert report.target_column.column_name == "PartyRoleID"
    assert report.citations[0].context is ColumnUsageContext.JOIN_ON
    assert report.citations[0].alias_used == "adr"
    assert report.citations[0].line == 283
