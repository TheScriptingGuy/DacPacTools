from __future__ import annotations

from dacpactools.application.use_cases._recommendation_rules import (
    LiveMetrics,
    recommend,
)
from dacpactools.domain.enums import (
    IndexKind,
    RecommendationImpact,
    SqlTargetPlatform,
    StatisticKind,
    SynapseDistributionKind,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.physical_design import (
    ColumnLiveMetric,
    ColumnUsageProfile,
    ExistingIndex,
    ExistingStatistic,
    TableUsageProfile,
)


def _table(schema: str = "dbo", name: str = "Fact") -> ObjectRef:
    return ObjectRef(database=None, schema=schema, name=name)


def _col(table: ObjectRef, name: str) -> ColumnRef:
    return ColumnRef(owner=table, column_name=name)


def _consumer(schema: str, name: str) -> ObjectRef:
    return ObjectRef(database=None, schema=schema, name=name)


def _profile(
    columns: list[ColumnUsageProfile],
    *,
    row_count: int | None = None,
    size_mb: float | None = None,
    is_staging: bool = False,
) -> TableUsageProfile:
    table = columns[0].column.owner if columns else _table()
    return TableUsageProfile(
        table=table,
        columns=tuple(columns),
        total_consumers=sum(len(c.consumers) for c in columns),
        row_count=row_count,
        size_mb=size_mb,
        is_staging_name=is_staging,
    )


def test_join_column_gets_single_column_stat_on_azure_sql_db() -> None:
    t = _table()
    col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=3,
        consumers=(_consumer("dbo", "vA"), _consumer("dbo", "vB")),
    )
    out = recommend(_profile([col]), (), (), SqlTargetPlatform.AZURE_SQL_DB)
    assert any(
        s.kind is StatisticKind.SINGLE_COLUMN and s.columns == ("CustomerKey",)
        for s in out.statistics
    )


def test_synapse_join_stat_is_high_impact() -> None:
    t = _table()
    col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=1,
        consumers=(_consumer("dbo", "vA"),),
    )
    out = recommend(_profile([col]), (), (), SqlTargetPlatform.SYNAPSE_DEDICATED_POOL)
    stats = [s for s in out.statistics if s.columns == ("CustomerKey",)]
    assert stats and stats[0].impact is RecommendationImpact.HIGH


def test_existing_single_column_stat_is_skipped() -> None:
    t = _table()
    col = ColumnUsageProfile(
        column=_col(t, "OrderId"),
        join_on_count=2,
        consumers=(_consumer("dbo", "v"),),
    )
    existing = (
        ExistingStatistic(
            table=t, name="stat_x", kind=StatisticKind.SINGLE_COLUMN, columns=("OrderId",)
        ),
    )
    out = recommend(_profile([col]), (), existing, SqlTargetPlatform.AZURE_SQL_DB)
    assert not any(s.columns == ("OrderId",) for s in out.statistics)


def test_nci_recommended_for_join_column_on_sql_family() -> None:
    t = _table()
    join_col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=2,
        consumers=(_consumer("dbo", "v"),),
    )
    other = ColumnUsageProfile(
        column=_col(t, "Amount"),
        projection_count=4,
    )
    out = recommend(_profile([join_col, other]), (), (), SqlTargetPlatform.AZURE_SQL_DB)
    nci = [i for i in out.indexes if i.kind is IndexKind.NONCLUSTERED]
    assert any(i.key_columns == ("CustomerKey",) for i in nci)
    hit = next(i for i in nci if i.key_columns == ("CustomerKey",))
    assert "Amount" in hit.include_columns


def test_nci_omits_include_on_synapse_dedicated() -> None:
    t = _table()
    join_col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=2,
        consumers=(_consumer("dbo", "v"),),
    )
    other = ColumnUsageProfile(column=_col(t, "Amount"), projection_count=4)
    out = recommend(
        _profile([join_col, other]),
        (),
        (),
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
    )
    ncis = [i for i in out.indexes if i.kind is IndexKind.NONCLUSTERED]
    for i in ncis:
        assert i.include_columns == ()


def test_existing_leading_key_skips_nci() -> None:
    t = _table()
    join_col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=2,
        consumers=(_consumer("dbo", "v"),),
    )
    existing = (
        ExistingIndex(
            table=t,
            name="IX_existing",
            kind=IndexKind.NONCLUSTERED,
            key_columns=("CustomerKey", "SnapshotDate"),
        ),
    )
    out = recommend(_profile([join_col]), existing, (), SqlTargetPlatform.AZURE_SQL_DB)
    assert not any(
        i.kind is IndexKind.NONCLUSTERED and i.key_columns == ("CustomerKey",)
        for i in out.indexes
    )
    assert any("existing" in s.lower() for s in out.skipped)


def test_fabric_warehouse_emits_stats_only() -> None:
    t = _table()
    col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=2,
        consumers=(_consumer("dbo", "vA"),),
    )
    out = recommend(_profile([col]), (), (), SqlTargetPlatform.FABRIC_WAREHOUSE)
    assert out.indexes == ()
    assert out.distribution is None
    assert out.statistics
    assert any("Fabric" in w for w in out.warnings)


def test_fabric_cluster_by_from_where_predicates() -> None:
    t = _table()
    where_col = ColumnUsageProfile(
        column=_col(t, "SaleDate"),
        where_count=6,
        where_equality_count=2,
        consumers=(_consumer("dbo", "vA"), _consumer("dbo", "vB")),
    )
    join_col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=4,
        consumers=(_consumer("dbo", "vC"),),
    )
    out = recommend(
        _profile([where_col, join_col], row_count=10_000_000),
        (),
        (),
        SqlTargetPlatform.FABRIC_WAREHOUSE,
    )
    assert out.cluster_by is not None
    # Only WHERE columns qualify — JOIN-only column must not appear.
    assert "SaleDate" in out.cluster_by.columns
    assert "CustomerKey" not in out.cluster_by.columns


def test_fabric_cluster_by_max_four_columns() -> None:
    t = _table()
    cols = [
        ColumnUsageProfile(
            column=_col(t, f"C{i}"),
            where_count=10 - i,
            consumers=(_consumer("dbo", "v"),),
        )
        for i in range(6)
    ]
    out = recommend(_profile(cols), (), (), SqlTargetPlatform.FABRIC_WAREHOUSE)
    assert out.cluster_by is not None
    assert len(out.cluster_by.columns) == 4
    # Highest-where-count columns win.
    assert out.cluster_by.columns[0] == "C0"


def test_fabric_cluster_by_excludes_unsupported_types() -> None:
    t = _table()
    bad_col = ColumnRef(owner=t, column_name="RowGuid", data_type="uniqueidentifier")
    good_col = ColumnRef(owner=t, column_name="SaleDate", data_type="date")
    profile = TableUsageProfile(
        table=t,
        columns=(
            ColumnUsageProfile(
                column=bad_col,
                where_count=5,
                consumers=(_consumer("dbo", "v"),),
            ),
            ColumnUsageProfile(
                column=good_col,
                where_count=3,
                consumers=(_consumer("dbo", "v"),),
            ),
        ),
        total_consumers=1,
    )
    out = recommend(profile, (), (), SqlTargetPlatform.FABRIC_WAREHOUSE)
    assert out.cluster_by is not None
    assert "RowGuid" not in out.cluster_by.columns
    assert "SaleDate" in out.cluster_by.columns
    excluded_names = {n for n, _ in out.cluster_by.excluded_columns}
    assert "RowGuid" in excluded_names


def test_fabric_cluster_by_skipped_when_no_where_columns() -> None:
    t = _table()
    join_only = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=5,
        consumers=(_consumer("dbo", "v"),),
    )
    out = recommend(_profile([join_only]), (), (), SqlTargetPlatform.FABRIC_WAREHOUSE)
    assert out.cluster_by is None


def test_fabric_cluster_by_verification_queries_when_row_count_unknown() -> None:
    t = _table()
    where_col = ColumnUsageProfile(
        column=_col(t, "SaleDate"),
        where_count=4,
        consumers=(_consumer("dbo", "v"),),
    )
    out = recommend(_profile([where_col]), (), (), SqlTargetPlatform.FABRIC_WAREHOUSE)
    assert out.cluster_by is not None
    assert out.cluster_by.is_guess is True
    assert out.cluster_by.verification_queries
    joined = "\n".join(out.cluster_by.verification_queries)
    assert "data_clustering_ordinal" in joined
    assert "COUNT_BIG(DISTINCT" in joined


def test_serverless_pool_emits_stats_only() -> None:
    t = _table()
    col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=1,
        consumers=(_consumer("dbo", "v"),),
    )
    out = recommend(_profile([col]), (), (), SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL)
    assert out.indexes == ()
    assert out.distribution is None
    assert out.statistics
    assert any("Serverless" in w for w in out.warnings)


def test_synapse_distribution_hash_confident_when_live_metrics_healthy() -> None:
    t = _table()
    join_col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=4,
        consumers=(_consumer("dbo", "vA"), _consumer("dbo", "vB")),
    )
    live = LiveMetrics(
        columns=(
            ColumnLiveMetric(
                column_name="CustomerKey", distinct_count=1_000_000, skew_ratio=1.1
            ),
        )
    )
    out = recommend(
        _profile([join_col], row_count=200_000_000),
        (),
        (),
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
        live=live,
    )
    d = out.distribution
    assert d is not None
    assert d.kind is SynapseDistributionKind.HASH
    assert d.hash_column == "CustomerKey"
    assert d.is_guess is False
    assert d.verification_queries == ()
    assert d.impact is RecommendationImpact.HIGH


def test_synapse_hash_guess_emits_verification_queries() -> None:
    t = _table()
    join_col = ColumnUsageProfile(
        column=_col(t, "CustomerKey"),
        join_on_count=4,
        consumers=(_consumer("dbo", "vA"),),
    )
    out = recommend(
        _profile([join_col]),
        (),
        (),
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
        live=None,
    )
    d = out.distribution
    assert d is not None
    assert d.kind is SynapseDistributionKind.HASH
    assert d.is_guess is True
    assert len(d.verification_queries) >= 2
    joined = "\n".join(d.verification_queries)
    assert "COUNT_BIG(DISTINCT" in joined
    assert "skew_ratio" in joined


def test_synapse_replicate_for_small_table_without_join_key() -> None:
    t = _table()
    col = ColumnUsageProfile(
        column=_col(t, "Descr"),
        where_count=1,
        consumers=(_consumer("dbo", "v"),),
    )
    out = recommend(
        _profile([col], size_mb=100.0, row_count=50_000),
        (),
        (),
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
    )
    assert out.distribution is not None
    assert out.distribution.kind is SynapseDistributionKind.REPLICATE
    assert out.distribution.is_guess is False


def test_synapse_round_robin_for_staging_table() -> None:
    t = _table(schema="stg", name="stg_raw_orders")
    col = ColumnUsageProfile(column=_col(t, "OrderId"))
    profile = TableUsageProfile(
        table=t,
        columns=(col,),
        total_consumers=0,
        is_staging_name=True,
    )
    out = recommend(profile, (), (), SqlTargetPlatform.SYNAPSE_DEDICATED_POOL)
    assert out.distribution is not None
    assert out.distribution.kind is SynapseDistributionKind.ROUND_ROBIN


def test_synapse_default_round_robin_emits_verification_queries_when_unknown() -> None:
    t = _table()
    col = ColumnUsageProfile(column=_col(t, "X"))
    out = recommend(_profile([col]), (), (), SqlTargetPlatform.SYNAPSE_DEDICATED_POOL)
    d = out.distribution
    assert d is not None
    assert d.kind is SynapseDistributionKind.ROUND_ROBIN
    assert d.is_guess is True
    assert d.verification_queries
