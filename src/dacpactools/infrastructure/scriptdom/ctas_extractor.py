from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dacpactools.domain.enums import SynapseDistributionKind, SynapseIndexKind
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.lineage import SynapseTableSpec
from dacpactools.infrastructure.scriptdom.ast_utils import (
    multipart_parts,
    schema_object_parts,
    type_name,
)


@dataclass(frozen=True)
class CtasSpec:
    target: ObjectRef
    spec: SynapseTableSpec


def extract_ctas(stmt: Any) -> CtasSpec | None:
    """Return CTAS distribution/index spec for a CreateTableStatement, or None if
    not a CTAS (no SelectStatement) / no WITH options."""
    if type_name(stmt) != "CreateTableStatement":
        return None
    if getattr(stmt, "SelectStatement", None) is None:
        return None
    db, schema, name = schema_object_parts(stmt.SchemaObjectName)
    if not name:
        return None
    target = ObjectRef(database=db, schema=schema or "dbo", name=name)

    dist_kind = SynapseDistributionKind.UNKNOWN
    dist_cols: tuple[str, ...] = ()
    idx_kind = SynapseIndexKind.NONE
    idx_cols: tuple[str, ...] = ()

    for opt in getattr(stmt, "Options", None) or []:
        ot = type_name(opt)
        if ot == "TableDistributionOption":
            k, cols = _read_distribution(opt)
            dist_kind = k
            dist_cols = cols
        elif ot == "TableIndexOption":
            k, cols = _read_index(opt)
            idx_kind = k
            idx_cols = cols

    if (
        dist_kind is SynapseDistributionKind.UNKNOWN
        and idx_kind is SynapseIndexKind.NONE
    ):
        return None

    return CtasSpec(
        target=target,
        spec=SynapseTableSpec(
            distribution_kind=dist_kind,
            distribution_columns=dist_cols,
            index_kind=idx_kind,
            index_columns=idx_cols,
            source="CTAS",
        ),
    )


def _read_distribution(opt: Any) -> tuple[SynapseDistributionKind, tuple[str, ...]]:
    value = getattr(opt, "Value", None)
    if value is None:
        return SynapseDistributionKind.UNKNOWN, ()
    vt = type_name(value)
    if "Hash" in vt:
        cols = _read_identifiers(getattr(value, "DistributionColumns", None))
        return SynapseDistributionKind.HASH, cols
    if "RoundRobin" in vt:
        return SynapseDistributionKind.ROUND_ROBIN, ()
    if "Replicate" in vt:
        return SynapseDistributionKind.REPLICATE, ()
    return SynapseDistributionKind.UNKNOWN, ()


def _read_index(opt: Any) -> tuple[SynapseIndexKind, tuple[str, ...]]:
    value = getattr(opt, "Value", None)
    if value is None:
        return SynapseIndexKind.NONE, ()
    vt = type_name(value)
    # HEAP is represented as TableNonClusteredIndexType (no clustered index).
    if "NonClustered" in vt:
        return SynapseIndexKind.HEAP, ()
    if "Clustered" in vt:
        if bool(getattr(value, "ColumnStore", False)):
            cols = _read_column_with_sort(getattr(value, "OrderedColumns", None))
            return SynapseIndexKind.CLUSTERED_COLUMNSTORE, cols
        cols = _read_column_with_sort(getattr(value, "Columns", None))
        return SynapseIndexKind.CLUSTERED, cols
    return SynapseIndexKind.NONE, ()


def _read_identifiers(collection: Any) -> tuple[str, ...]:
    if collection is None:
        return ()
    out: list[str] = []
    try:
        items = list(collection)
    except Exception:  # noqa: BLE001
        return ()
    for it in items:
        val = getattr(it, "Value", None)
        if val:
            out.append(str(val))
    return tuple(out)


def _read_column_with_sort(collection: Any) -> tuple[str, ...]:
    """Collection of ColumnWithSortOrder or ColumnReferenceExpression -> column names."""
    if collection is None:
        return ()
    out: list[str] = []
    try:
        items = list(collection)
    except Exception:  # noqa: BLE001
        return ()
    for it in items:
        col_ref = getattr(it, "Column", it)
        mpi = getattr(col_ref, "MultiPartIdentifier", None)
        parts = multipart_parts(mpi) if mpi is not None else []
        if parts:
            out.append(parts[-1])
    return tuple(out)
