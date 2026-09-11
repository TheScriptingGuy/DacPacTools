"""Pure-function recommendation rules. No I/O, no ports — feed profiles + existing
metadata + platform, receive recommendation tuples."""
from __future__ import annotations

from dataclasses import dataclass

from dacpactools.domain.enums import (
    IndexKind,
    RecommendationImpact,
    SqlTargetPlatform,
    StatisticKind,
    SynapseDistributionKind,
)
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.physical_design import (
    ClusterByRecommendation,
    ColumnLiveMetric,
    ColumnUsageProfile,
    DistributionRecommendation,
    ExistingIndex,
    ExistingStatistic,
    IndexRecommendation,
    StatisticRecommendation,
    TableUsageProfile,
)

REPLICATE_SIZE_MB_THRESHOLD: float = 2048.0
LARGE_TABLE_ROW_THRESHOLD: int = 1_000_000
CCI_ROW_THRESHOLD: int = 1_000_000
SYNAPSE_LARGE_TABLE_ROW_THRESHOLD: int = 60_000_000
WIDE_TABLE_COLUMN_COUNT: int = 10
SKEW_HEALTHY_MAX: float = 2.0
FABRIC_CLUSTER_BY_MAX_COLUMNS: int = 4

# Fabric Warehouse CLUSTER BY data type exclusions (per learn.microsoft.com data-clustering).
_FABRIC_CLUSTER_BY_EXCLUDED_TYPES: frozenset[str] = frozenset(
    {
        "bit",
        "varchar(max)",
        "varbinary(max)",
        "varbinary",
        "uniqueidentifier",
    }
)


@dataclass(frozen=True)
class LiveMetrics:
    """Live-DB samples used by rules. Empty tuple = no data (offline run)."""
    columns: tuple[ColumnLiveMetric, ...] = ()
    existing_distribution_kind: SynapseDistributionKind | None = None
    existing_distribution_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuleOutput:
    indexes: tuple[IndexRecommendation, ...]
    statistics: tuple[StatisticRecommendation, ...]
    distribution: DistributionRecommendation | None
    cluster_by: ClusterByRecommendation | None
    skipped: tuple[str, ...]
    warnings: tuple[str, ...]


def recommend(
    profile: TableUsageProfile,
    existing_indexes: tuple[ExistingIndex, ...],
    existing_stats: tuple[ExistingStatistic, ...],
    platform: SqlTargetPlatform,
    live: LiveMetrics | None = None,
) -> RuleOutput:
    live = live or LiveMetrics()
    warnings: list[str] = []

    if platform.is_stats_only:
        stats = _recommend_statistics(profile, existing_stats, platform)
        cluster_by: ClusterByRecommendation | None = None
        warnings_list: list[str] = []
        if platform is SqlTargetPlatform.FABRIC_WAREHOUSE:
            warnings_list.append(
                "Microsoft Fabric Warehouse does not expose user-managed indexes "
                "or distribution keys; only CLUSTER BY + statistics recommendations "
                "emitted. Auto-statistics exist but explicit CREATE STATISTICS "
                "WITH FULLSCAN on JOIN/filter keys stabilises plan choice."
            )
            cluster_by = _recommend_cluster_by_fabric(profile)
        else:
            warnings_list.append(
                "Serverless SQL Pool does not support persistent indexes; "
                "only statistics recommendations emitted."
            )
        return RuleOutput(
            indexes=(),
            statistics=stats,
            distribution=None,
            cluster_by=cluster_by,
            skipped=(),
            warnings=tuple(warnings_list),
        )

    stats = _recommend_statistics(profile, existing_stats, platform)
    if platform.is_mpp:
        indexes, idx_skipped = _recommend_indexes_synapse(profile, existing_indexes)
        distribution = _recommend_distribution_synapse(profile, live)
    else:
        indexes, idx_skipped = _recommend_indexes_sql_server_family(
            profile, existing_indexes, platform
        )
        distribution = None

    stat_skipped = _skipped_stat_names(profile, existing_stats)
    return RuleOutput(
        indexes=indexes,
        statistics=stats,
        distribution=distribution,
        cluster_by=None,
        skipped=tuple(idx_skipped) + tuple(stat_skipped),
        warnings=tuple(warnings),
    )


def _recommend_statistics(
    profile: TableUsageProfile,
    existing: tuple[ExistingStatistic, ...],
    platform: SqlTargetPlatform,
) -> tuple[StatisticRecommendation, ...]:
    out: list[StatisticRecommendation] = []
    existing_sets = {frozenset(s.columns) for s in existing}

    for col in profile.columns:
        col_name = col.column.column_name
        if col.join_on_count >= 1 and frozenset({col_name}) not in existing_sets:
            impact = (
                RecommendationImpact.HIGH if platform.is_mpp else RecommendationImpact.MEDIUM
            )
            reason = (
                f"column [{col_name}] participates in JOIN_ON across "
                f"{len(col.consumers)} consumer(s)"
            )
            if platform.is_mpp:
                reason += "; Synapse Dedicated Pool does not auto-create statistics"
            else:
                reason += "; explicit stats stabilise cardinality estimates"
            out.append(
                StatisticRecommendation(
                    table=profile.table,
                    kind=StatisticKind.SINGLE_COLUMN,
                    columns=(col_name,),
                    impact=impact,
                    rationale=reason,
                    driving_citations=col.consumers,
                )
            )
            continue

        if (
            col.where_equality_count >= 2
            and frozenset({col_name}) not in existing_sets
        ):
            out.append(
                StatisticRecommendation(
                    table=profile.table,
                    kind=StatisticKind.SINGLE_COLUMN,
                    columns=(col_name,),
                    impact=RecommendationImpact.MEDIUM,
                    rationale=(
                        f"column [{col_name}] appears in equality WHERE filters in "
                        f"{col.where_equality_count} distinct citation(s)"
                    ),
                    driving_citations=col.consumers,
                )
            )

    group_cols = tuple(
        c.column.column_name
        for c in profile.columns
        if (c.group_by_count + c.partition_by_count) >= 2
    )
    if len(group_cols) >= 2 and frozenset(group_cols) not in existing_sets:
        driving = _dedup_refs(
            r for c in profile.columns if c.column.column_name in group_cols for r in c.consumers
        )
        out.append(
            StatisticRecommendation(
                table=profile.table,
                kind=StatisticKind.MULTI_COLUMN,
                columns=group_cols,
                impact=RecommendationImpact.MEDIUM,
                rationale=(
                    "columns co-occur in GROUP BY / PARTITION BY across consumers; "
                    "multi-column histogram improves cardinality estimates"
                ),
                driving_citations=driving,
            )
        )
    return tuple(out)


def _recommend_indexes_sql_server_family(
    profile: TableUsageProfile,
    existing: tuple[ExistingIndex, ...],
    platform: SqlTargetPlatform,
) -> tuple[tuple[IndexRecommendation, ...], list[str]]:
    out: list[IndexRecommendation] = []
    skipped: list[str] = []

    has_clustered = any(i.kind is IndexKind.CLUSTERED for i in existing)
    has_cci = any(
        i.kind in {IndexKind.CLUSTERED_COLUMNSTORE, IndexKind.NONCLUSTERED_COLUMNSTORE}
        for i in existing
    )

    top_where = max(
        profile.columns,
        key=lambda c: (c.where_equality_count, c.where_count),
        default=None,
    )
    if (
        not has_clustered
        and top_where is not None
        and top_where.where_equality_count >= 3
        and (profile.row_count or 0) >= LARGE_TABLE_ROW_THRESHOLD
        and top_where.where_equality_count > top_where.join_on_count
    ):
        out.append(
            IndexRecommendation(
                table=profile.table,
                kind=IndexKind.CLUSTERED,
                key_columns=(top_where.column.column_name,),
                impact=RecommendationImpact.HIGH,
                rationale=(
                    f"heavy equality-WHERE column ({top_where.where_equality_count} "
                    "citations) on a large heap — clustered on this key reduces lookups"
                ),
                driving_citations=top_where.consumers,
            )
        )

    is_wide = len(profile.columns) >= WIDE_TABLE_COLUMN_COUNT
    total_group_by = sum(c.group_by_count for c in profile.columns)
    total_projection = sum(c.projection_count for c in profile.columns)
    large_or_unknown = profile.row_count is None or profile.row_count >= CCI_ROW_THRESHOLD
    if (
        not has_cci
        and platform.supports_columnstore
        and is_wide
        and (total_group_by + total_projection) >= 5
        and large_or_unknown
    ):
        out.append(
            IndexRecommendation(
                table=profile.table,
                kind=IndexKind.NONCLUSTERED_COLUMNSTORE,
                key_columns=(),
                impact=RecommendationImpact.HIGH,
                rationale=(
                    "wide table with heavy GROUP BY / projection breadth across consumers "
                    "— columnstore accelerates analytical scans"
                ),
                driving_citations=_dedup_refs(
                    r for c in profile.columns for r in c.consumers
                ),
            )
        )

    top_projected = _top_columns_by(profile, key=lambda c: c.projection_count, n=3)
    for col in profile.columns:
        col_name = col.column.column_name
        if col.join_on_count < 1:
            continue
        if _existing_covers_leading(existing, (col_name,), only_kinds={IndexKind.NONCLUSTERED, IndexKind.CLUSTERED}):
            skipped.append(f"NCI on ({col_name}) — covered by existing index")
            continue
        include_cols: tuple[str, ...] = ()
        if platform.supports_included_columns:
            include_cols = tuple(
                c for c in top_projected if c != col_name
            )[:3]
        out.append(
            IndexRecommendation(
                table=profile.table,
                kind=IndexKind.NONCLUSTERED,
                key_columns=(col_name,),
                include_columns=include_cols,
                impact=RecommendationImpact.MEDIUM,
                rationale=(
                    f"column [{col_name}] is a JOIN_ON key in "
                    f"{len(col.consumers)} consumer(s)"
                ),
                driving_citations=col.consumers,
            )
        )
    return tuple(out), skipped


def _recommend_indexes_synapse(
    profile: TableUsageProfile,
    existing: tuple[ExistingIndex, ...],
) -> tuple[tuple[IndexRecommendation, ...], list[str]]:
    out: list[IndexRecommendation] = []
    skipped: list[str] = []

    has_cci = any(i.kind is IndexKind.CLUSTERED_COLUMNSTORE for i in existing)
    row_count = profile.row_count or 0
    is_wide = len(profile.columns) >= WIDE_TABLE_COLUMN_COUNT
    total_projection = sum(c.projection_count for c in profile.columns)
    cci_recommended = False
    if (
        not has_cci
        and is_wide
        and total_projection >= 5
        and (row_count == 0 or row_count > SYNAPSE_LARGE_TABLE_ROW_THRESHOLD)
    ):
        out.append(
            IndexRecommendation(
                table=profile.table,
                kind=IndexKind.CLUSTERED_COLUMNSTORE,
                key_columns=(),
                impact=RecommendationImpact.HIGH,
                rationale=(
                    "analytical fact-like table on Synapse Dedicated Pool — "
                    "clustered columnstore is the default choice for large wide tables"
                ),
                driving_citations=_dedup_refs(
                    r for c in profile.columns for r in c.consumers
                ),
            )
        )
        cci_recommended = True

    if not cci_recommended:
        for col in profile.columns:
            col_name = col.column.column_name
            if col.join_on_count < 1 and col.where_equality_count < 2:
                continue
            if _existing_covers_leading(
                existing,
                (col_name,),
                only_kinds={IndexKind.NONCLUSTERED, IndexKind.CLUSTERED},
            ):
                skipped.append(f"NCI on ({col_name}) — covered by existing index")
                continue
            out.append(
                IndexRecommendation(
                    table=profile.table,
                    kind=IndexKind.NONCLUSTERED,
                    key_columns=(col_name,),
                    impact=RecommendationImpact.MEDIUM,
                    rationale=(
                        f"column [{col_name}] used as JOIN_ON or equality WHERE "
                        f"key — no CCI recommended for this table"
                    ),
                    driving_citations=col.consumers,
                )
            )
    return tuple(out), skipped


def _recommend_distribution_synapse(
    profile: TableUsageProfile,
    live: LiveMetrics,
) -> DistributionRecommendation:
    table = profile.table
    metrics_by_col = {m.column_name: m for m in live.columns}

    if profile.is_staging_name and _no_join_usage(profile):
        return DistributionRecommendation(
            table=table,
            kind=SynapseDistributionKind.ROUND_ROBIN,
            impact=RecommendationImpact.MEDIUM,
            rationale=(
                "staging / landing table (name pattern) with no JOIN_ON usage — "
                "ROUND_ROBIN keeps load evenly distributed"
            ),
        )

    ranked = sorted(
        profile.columns,
        key=lambda c: (c.join_on_count, len(c.consumers), -_lex(c.column.column_name)),
        reverse=True,
    )
    top_join_col = ranked[0] if ranked and ranked[0].join_on_count >= 1 else None

    if top_join_col is not None:
        col_name = top_join_col.column.column_name
        metric = metrics_by_col.get(col_name)
        distinct = metric.distinct_count if metric else None
        skew = metric.skew_ratio if metric else None
        is_guess = distinct is None or skew is None
        confident_hash = (
            distinct is not None
            and distinct >= 10_000
            and skew is not None
            and skew <= SKEW_HEALTHY_MAX
        )
        if confident_hash:
            rationale = (
                f"column [{col_name}] is the top JOIN_ON key across "
                f"{len(top_join_col.consumers)} consumer(s); distinct={distinct}, "
                f"skew={skew:.2f} — HASH minimises shuffle"
            )
            impact = RecommendationImpact.HIGH
            queries: tuple[str, ...] = ()
        else:
            rationale = (
                f"column [{col_name}] is the top JOIN_ON key across "
                f"{len(top_join_col.consumers)} consumer(s); cardinality/skew unknown"
                " — run the queries below before applying HASH"
            )
            impact = RecommendationImpact.MEDIUM
            queries = _distribution_verification_queries(table, col_name)
        return DistributionRecommendation(
            table=table,
            kind=SynapseDistributionKind.HASH,
            hash_column=col_name,
            impact=impact,
            rationale=rationale,
            driving_citations=top_join_col.consumers,
            verification_queries=queries,
            is_guess=is_guess,
        )

    size_mb = profile.size_mb
    row_count = profile.row_count
    small_dimension = (
        (size_mb is not None and size_mb < REPLICATE_SIZE_MB_THRESHOLD)
        or (row_count is not None and row_count < 100_000_000)
    )
    if small_dimension:
        is_guess = size_mb is None and row_count is None
        rationale = (
            "table has no dominant JOIN_ON key and is small — REPLICATE "
            "eliminates shuffle at low storage cost"
        )
        if is_guess:
            rationale += " (size/row-count unknown — confirm with queries below)"
        return DistributionRecommendation(
            table=table,
            kind=SynapseDistributionKind.REPLICATE,
            impact=RecommendationImpact.MEDIUM,
            rationale=rationale,
            verification_queries=_size_verification_queries(table) if is_guess else (),
            is_guess=is_guess,
        )

    return DistributionRecommendation(
        table=table,
        kind=SynapseDistributionKind.ROUND_ROBIN,
        impact=RecommendationImpact.LOW,
        rationale=(
            "no dominant JOIN_ON key detected and table size unknown — "
            "ROUND_ROBIN is the safe default; verify with the queries below"
        ),
        verification_queries=(
            _size_verification_queries(table)
            + _distribution_verification_queries(table, "<candidate_key>")
        ),
        is_guess=True,
    )


def _recommend_cluster_by_fabric(
    profile: TableUsageProfile,
) -> ClusterByRecommendation | None:
    """Pick up to 4 WHERE-predicate columns for Fabric CLUSTER BY. Per Microsoft
    docs, JOIN equality does NOT benefit; only WHERE predicates do."""
    excluded: list[tuple[str, str]] = []
    candidates: list[tuple[ColumnUsageProfile, int]] = []
    for col in profile.columns:
        weight = col.where_count
        if weight < 1:
            continue
        reason = _cluster_by_exclusion_reason(col)
        if reason is not None:
            excluded.append((col.column.column_name, reason))
            continue
        candidates.append((col, weight))

    if not candidates:
        return None

    candidates.sort(
        key=lambda p: (p[1], p[0].where_equality_count, -_lex(p[0].column.column_name)),
        reverse=True,
    )
    picked = candidates[:FABRIC_CLUSTER_BY_MAX_COLUMNS]
    columns = tuple(p[0].column.column_name for p in picked)

    row_count = profile.row_count
    large = row_count is None or row_count >= LARGE_TABLE_ROW_THRESHOLD
    impact = RecommendationImpact.HIGH if large else RecommendationImpact.MEDIUM
    rationale = (
        f"columns {list(columns)} appear in WHERE predicates across consumers "
        f"({', '.join(str(p[0].where_count) for p in picked)} citations respectively); "
        "Fabric CLUSTER BY colocates rows to enable file skipping. "
        "Note: JOIN equality does not benefit from CLUSTER BY per Microsoft docs."
    )
    is_guess = row_count is None
    verification = _cluster_by_verification_queries(profile.table, columns) if is_guess else ()
    driving = _dedup_refs(
        r for p in picked for r in p[0].consumers
    )
    return ClusterByRecommendation(
        table=profile.table,
        columns=columns,
        impact=impact,
        rationale=rationale,
        driving_citations=driving,
        verification_queries=verification,
        is_guess=is_guess,
        excluded_columns=tuple(excluded),
    )


def _cluster_by_exclusion_reason(col: ColumnUsageProfile) -> str | None:
    dt = (col.column.data_type or "").strip().lower()
    if not dt:
        return None  # offline / unknown — user must verify manually
    normalized = _normalize_type(dt)
    if normalized in _FABRIC_CLUSTER_BY_EXCLUDED_TYPES:
        return f"data type {col.column.data_type} unsupported for CLUSTER BY"
    return None


def _normalize_type(dt: str) -> str:
    lowered = dt.strip().lower()
    if lowered.startswith("varchar") and "max" in lowered:
        return "varchar(max)"
    if lowered.startswith("varbinary") and "max" in lowered:
        return "varbinary(max)"
    if lowered.startswith("varbinary"):
        return "varbinary"
    return lowered


def _cluster_by_verification_queries(
    table: ObjectRef, columns: tuple[str, ...]
) -> tuple[str, ...]:
    q_cardinality = (
        "-- distinct counts on candidate CLUSTER BY columns (mid-high cardinality is best)\n"
        + "SELECT "
        + ", ".join(f"COUNT_BIG(DISTINCT [{c}]) AS distinct_{c}" for c in columns)
        + ", COUNT_BIG(*) AS total_rows\n"
        + f"FROM [{table.schema_name}].[{table.name}];"
    )
    q_existing = (
        f"-- confirm no existing CLUSTER BY on [{table.schema_name}].[{table.name}]\n"
        "SELECT c.name AS column_name, ic.data_clustering_ordinal\n"
        "FROM sys.tables t\n"
        "JOIN sys.columns c ON t.object_id = c.object_id\n"
        "JOIN sys.index_columns ic ON c.object_id = ic.object_id "
        "AND c.column_id = ic.column_id\n"
        "JOIN sys.schemas s ON s.schema_id = t.schema_id\n"
        f"WHERE s.name = '{table.schema_name}' AND t.name = '{table.name}'\n"
        "  AND ic.data_clustering_ordinal > 0\n"
        "ORDER BY ic.data_clustering_ordinal;"
    )
    return (q_existing, q_cardinality)


def _existing_covers_leading(
    existing: tuple[ExistingIndex, ...],
    candidate_keys: tuple[str, ...],
    only_kinds: set[IndexKind],
) -> bool:
    if not candidate_keys:
        return False
    cand_lower = tuple(c.lower() for c in candidate_keys)
    for idx in existing:
        if idx.kind not in only_kinds:
            continue
        existing_lower = tuple(c.lower() for c in idx.key_columns)
        if len(existing_lower) < len(cand_lower):
            continue
        if existing_lower[: len(cand_lower)] == cand_lower:
            return True
    return False


def _skipped_stat_names(
    profile: TableUsageProfile,
    existing: tuple[ExistingStatistic, ...],
) -> list[str]:
    if not existing:
        return []
    covered = {frozenset(s.columns) for s in existing}
    hits: list[str] = []
    for c in profile.columns:
        if (
            c.join_on_count >= 1
            and frozenset({c.column.column_name}) in covered
        ):
            hits.append(f"stats on ({c.column.column_name}) — covered by existing statistic")
    return hits


def _top_columns_by(profile: TableUsageProfile, key, n: int) -> tuple[str, ...]:
    ranked = sorted(profile.columns, key=lambda c: (key(c), c.column.column_name), reverse=True)
    return tuple(c.column.column_name for c in ranked[:n])


def _dedup_refs(iterable) -> tuple[ObjectRef, ...]:
    seen: set[tuple] = set()
    out: list[ObjectRef] = []
    for ref in iterable:
        k = ref.key()
        if k in seen:
            continue
        seen.add(k)
        out.append(ref)
    return tuple(out)


def _no_join_usage(profile: TableUsageProfile) -> bool:
    return all(c.join_on_count == 0 for c in profile.columns)


def _lex(name: str) -> int:
    return sum(ord(ch) for ch in name.lower())


def _size_verification_queries(table: ObjectRef) -> tuple[str, ...]:
    q_size = (
        "SELECT SUM(nps.reserved_page_count) * 8.0 / 1024 AS size_mb\n"
        "FROM sys.dm_pdw_nodes_db_partition_stats nps\n"
        "JOIN sys.pdw_nodes_tables nt\n"
        "    ON nps.object_id = nt.object_id AND nps.pdw_node_id = nt.pdw_node_id\n"
        "JOIN sys.pdw_table_mappings tm ON nt.name = tm.physical_name\n"
        "JOIN sys.objects o ON tm.object_id = o.object_id\n"
        "JOIN sys.schemas s ON o.schema_id = s.schema_id\n"
        f"WHERE s.name = '{table.schema_name}' AND o.name = '{table.name}';"
    )
    q_rows = (
        "SELECT SUM(row_count) AS total_rows\n"
        "FROM sys.dm_pdw_nodes_db_partition_stats\n"
        f"WHERE object_id = OBJECT_ID('{table.schema_name}.{table.name}');"
    )
    return (q_size, q_rows)


def _distribution_verification_queries(table: ObjectRef, column: str) -> tuple[str, ...]:
    q_distinct = (
        f"-- distinct values on candidate hash key [{column}]\n"
        f"SELECT COUNT_BIG(DISTINCT [{column}]) AS distinct_values,\n"
        f"       COUNT_BIG(*) AS total_rows\n"
        f"FROM [{table.schema_name}].[{table.name}];"
    )
    q_skew = (
        "-- per-distribution row-count skew (max/avg ratio; healthy < 2.0)\n"
        "SELECT MAX(row_count) * 1.0 / NULLIF(AVG(row_count * 1.0), 0) AS skew_ratio\n"
        "FROM (\n"
        "    SELECT pdw_node_id, distribution_id, SUM(row_count) AS row_count\n"
        "    FROM sys.dm_pdw_nodes_db_partition_stats\n"
        f"    WHERE object_id = OBJECT_ID('{table.schema_name}.{table.name}')\n"
        "    GROUP BY pdw_node_id, distribution_id\n"
        ") d;"
    )
    return (q_distinct, q_skew)
