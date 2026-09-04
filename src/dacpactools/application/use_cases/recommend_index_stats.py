from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dacpactools.application.dto.lineage_request import DacPacSource, LiveConnectionSource
from dacpactools.application.dto.recommend_index_stats import (
    RecommendIndexStatsRequest,
    RecommendIndexStatsResult,
)
from dacpactools.application.ports._handles import (
    ConnectionHandle,
    DacPacHandle,
    TSqlObjectHandle,
)
from dacpactools.application.ports.clock import IClock
from dacpactools.application.ports.dacpac_service import IDacPacService
from dacpactools.application.ports.scriptdom_parser import IScriptDomParser
from dacpactools.application.ports.sql_metadata_service import ISqlMetadataService
from dacpactools.application.ports.token_provider import ITokenProvider
from dacpactools.application.use_cases._recommendation_rules import (
    LiveMetrics,
    recommend,
)
from dacpactools.domain.column_usage import ColumnCitation, ColumnUsageContext
from dacpactools.domain.enums import (
    IndexKind,
    ObjectType,
    SqlTargetPlatform,
    StatisticKind,
    SynapseDistributionKind,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.joins import JoinUsage
from dacpactools.domain.physical_design import (
    ColumnLiveMetric,
    ColumnUsageProfile,
    ExistingIndex,
    ExistingStatistic,
    IndexRecommendation,
    StatisticRecommendation,
    TableRecommendation,
    TableUsageProfile,
)
from dacpactools.infrastructure.scriptdom.column_usage_finder import find_column_usage
from dacpactools.infrastructure.scriptdom.join_finder import find_joins_for
from dacpactools.infrastructure.writers.filesystem import atomic_write_text
from dacpactools.presentation.logging_config import get_logger

log = get_logger("recommend_index_stats")


_CONSUMER_TYPES: tuple[ObjectType, ...] = (
    ObjectType.VIEW,
    ObjectType.PROCEDURE,
    ObjectType.TABLE_VALUED_FUNCTION,
    ObjectType.SCALAR_FUNCTION,
)

_STAGING_NAME_RE = re.compile(r"^(stg|stage|raw|tmp|land|landing|src)[_.]", re.IGNORECASE)
_EQUALITY_RE = re.compile(r"(?<![<>!])=(?!=)")


@dataclass
class RecommendIndexStatsUseCase:
    dacpac: IDacPacService
    sql_meta: ISqlMetadataService
    parser: IScriptDomParser
    token_provider: ITokenProvider
    clock: IClock

    def execute(self, request: RecommendIndexStatsRequest) -> RecommendIndexStatsResult:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        handle = self._load_dacpac(request)
        conn: ConnectionHandle | None = None
        platform = SqlTargetPlatform.UNKNOWN
        warnings: list[str] = []

        if request.connection is not None:
            token = self.token_provider.get_token()
            conn = self.sql_meta.open(request.connection, token)
            platform = self.sql_meta.detect_target_platform(conn)
            if platform is SqlTargetPlatform.UNKNOWN:
                if request.target_override is not None:
                    platform = request.target_override
                    warnings.append(
                        "target platform auto-detection failed; using --target override"
                    )
                else:
                    warnings.append(
                        "target platform auto-detection returned UNKNOWN; recommendations may be inaccurate"
                    )
        else:
            if request.target_override is None:
                raise ValueError(
                    "target platform is required when no --connection is supplied"
                )
            platform = request.target_override

        try:
            tables = self._enumerate_tables(handle, request.schemas)
            consumers = self._collect_consumers(handle)
            profiles = self._build_profiles(tables, consumers, conn)
            recommendations: list[TableRecommendation] = []
            for profile, table_handle in profiles:
                existing_indexes = (
                    self.sql_meta.fetch_indexes(conn, profile.table)
                    if conn is not None
                    else ()
                )
                existing_stats = (
                    self.sql_meta.fetch_statistics(conn, profile.table)
                    if conn is not None
                    else ()
                )
                live = self._sample_live_metrics(conn, profile, platform)
                result = recommend(
                    profile=profile,
                    existing_indexes=existing_indexes,
                    existing_stats=existing_stats,
                    platform=platform,
                    live=live,
                )
                recommendations.append(
                    TableRecommendation(
                        profile=profile,
                        distribution=result.distribution,
                        cluster_by=result.cluster_by,
                        indexes=result.indexes,
                        statistics=result.statistics,
                        skipped_because_existing=result.skipped,
                        warnings=result.warnings,
                    )
                )
        finally:
            if conn is not None:
                self.sql_meta.close(conn)

        recommendations.sort(
            key=lambda r: (r.profile.table.schema_name.lower(), r.profile.table.name.lower())
        )
        artifacts = self._write_artifacts(recommendations, platform, request)
        log.info(
            "recommendations complete",
            tables=len(recommendations),
            indexes=sum(len(r.indexes) for r in recommendations),
            statistics=sum(len(r.statistics) for r in recommendations),
            platform=platform.value,
        )
        return RecommendIndexStatsResult(
            target_platform=platform,
            tables=tuple(recommendations),
            artifacts=artifacts,
            warnings=tuple(warnings),
        )

    def _load_dacpac(self, request: RecommendIndexStatsRequest) -> DacPacHandle:
        src = request.source
        if isinstance(src, DacPacSource):
            return self.dacpac.load(src.path)
        if isinstance(src, LiveConnectionSource):
            return self.dacpac.load_from_live(src.connection, request.output_dir / ".tmp")
        raise TypeError(f"unsupported source: {type(src).__name__}")

    def _enumerate_tables(
        self, handle: DacPacHandle, schemas: frozenset[str]
    ) -> list[TSqlObjectHandle]:
        schemas_lc = {s.lower() for s in schemas}
        out: list[TSqlObjectHandle] = []
        for obj in self.dacpac.list_all_objects(handle):
            if obj.ref.object_type is not ObjectType.TABLE:
                continue
            if obj.ref.schema_name.lower() not in schemas_lc:
                continue
            out.append(obj)
        return out

    def _collect_consumers(
        self, handle: DacPacHandle
    ) -> list[tuple[TSqlObjectHandle, Any]]:
        parsed: list[tuple[TSqlObjectHandle, Any]] = []
        for obj in self.dacpac.list_all_objects(handle):
            if obj.ref.object_type not in _CONSUMER_TYPES:
                continue
            script = self.dacpac.get_body_script(obj)
            if not script:
                continue
            batch = self.parser.parse(script)
            if batch.errors:
                log.debug(
                    "parse errors in consumer",
                    consumer=obj.ref.qualified(),
                    errors=batch.errors,
                )
                continue
            parsed.append((obj, batch.fragment))
        return parsed

    def _build_profiles(
        self,
        tables: list[TSqlObjectHandle],
        consumers: list[tuple[TSqlObjectHandle, Any]],
        conn: ConnectionHandle | None,
    ) -> list[tuple[TableUsageProfile, TSqlObjectHandle]]:
        out: list[tuple[TableUsageProfile, TSqlObjectHandle]] = []
        for table_handle in tables:
            table_ref = table_handle.ref
            columns = self.dacpac.get_columns(table_handle)
            per_col: dict[str, dict] = {
                c.column_name.lower(): _empty_col_bucket(c) for c in columns
            }
            join_partner_bucket: dict[str, set[tuple[tuple, str]]] = {
                c.column_name.lower(): set() for c in columns
            }
            all_consumers: set[tuple] = set()

            for consumer, fragment in consumers:
                consumer_ref = consumer.ref
                consumer_hit = False
                for col in columns:
                    citations, _warns = find_column_usage(
                        consumer_ref, fragment, table_ref, col.column_name
                    )
                    if not citations:
                        continue
                    consumer_hit = True
                    _fold_citations(per_col[col.column_name.lower()], citations, consumer_ref)
                try:
                    joins = find_joins_for(consumer_ref, fragment, table_ref)
                except Exception as e:  # noqa: BLE001
                    log.debug(
                        "join finder failed",
                        consumer=consumer_ref.qualified(),
                        err=str(e),
                    )
                    joins = []
                _fold_joins(join_partner_bucket, joins, per_col)
                if consumer_hit or joins:
                    all_consumers.add(consumer_ref.key())

            profile_cols: list[ColumnUsageProfile] = []
            for col in columns:
                key = col.column_name.lower()
                b = per_col[key]
                partners = tuple(
                    (_unkey_ref(other_key), other_col)
                    for other_key, other_col in sorted(join_partner_bucket[key])
                )
                profile_cols.append(
                    ColumnUsageProfile(
                        column=col,
                        join_on_count=b["join_on_count"],
                        where_count=b["where_count"],
                        where_equality_count=b["where_equality_count"],
                        group_by_count=b["group_by_count"],
                        order_by_count=b["order_by_count"],
                        partition_by_count=b["partition_by_count"],
                        projection_count=b["projection_count"],
                        join_partners=partners,
                        consumers=tuple(b["consumers"]),
                    )
                )

            row_count = None
            size_mb = None
            if conn is not None:
                row_count = self.sql_meta.fetch_row_count(conn, table_ref)
                size_mb = self.sql_meta.fetch_table_size_mb(conn, table_ref)
            profile = TableUsageProfile(
                table=table_ref,
                columns=tuple(profile_cols),
                total_consumers=len(all_consumers),
                row_count=row_count,
                size_mb=size_mb,
                is_staging_name=bool(_STAGING_NAME_RE.match(table_ref.name)),
            )
            out.append((profile, table_handle))
        return out

    def _sample_live_metrics(
        self,
        conn: ConnectionHandle | None,
        profile: TableUsageProfile,
        platform: SqlTargetPlatform,
    ) -> LiveMetrics:
        if conn is None or platform is not SqlTargetPlatform.SYNAPSE_DEDICATED_POOL:
            return LiveMetrics()
        skew = self.sql_meta.fetch_distribution_skew_ratio(conn, profile.table)
        existing_dist = self.sql_meta.fetch_synapse_distribution(conn, profile.table)
        top_join_cols = sorted(
            profile.columns,
            key=lambda c: (c.join_on_count, len(c.consumers)),
            reverse=True,
        )[:3]
        metrics: list[ColumnLiveMetric] = []
        for c in top_join_cols:
            if c.join_on_count < 1:
                continue
            distinct = self.sql_meta.fetch_column_distinct_count(
                conn, profile.table, c.column.column_name
            )
            metrics.append(
                ColumnLiveMetric(
                    column_name=c.column.column_name,
                    distinct_count=distinct,
                    skew_ratio=skew,
                )
            )
        existing_kind = None
        existing_cols: tuple[str, ...] = ()
        if existing_dist is not None:
            existing_kind, existing_cols = existing_dist
        return LiveMetrics(
            columns=tuple(metrics),
            existing_distribution_kind=existing_kind,
            existing_distribution_columns=existing_cols,
        )

    def _write_artifacts(
        self,
        recs: list[TableRecommendation],
        platform: SqlTargetPlatform,
        request: RecommendIndexStatsRequest,
    ) -> dict[str, "Any"]:
        ts = self.clock.now_utc().strftime("%Y%m%dT%H%M%SZ")
        sql_path = request.output_dir / f"recommend_index_stats_{ts}.sql"
        md_path = request.output_dir / f"recommend_index_stats_{ts}.md"
        atomic_write_text(sql_path, _render_sql(recs, platform, ts))
        atomic_write_text(md_path, _render_markdown(recs, platform, ts))
        return {"sql": sql_path, "md": md_path}


def _empty_col_bucket(col: ColumnRef) -> dict:
    return {
        "column": col,
        "join_on_count": 0,
        "where_count": 0,
        "where_equality_count": 0,
        "group_by_count": 0,
        "order_by_count": 0,
        "partition_by_count": 0,
        "projection_count": 0,
        "consumers": [],
        "_consumer_keys": set(),
    }


def _fold_citations(
    bucket: dict,
    citations: list[ColumnCitation],
    consumer_ref: ObjectRef,
) -> None:
    for cit in citations:
        ctx = cit.context
        if ctx is ColumnUsageContext.JOIN_ON:
            bucket["join_on_count"] += 1
        elif ctx is ColumnUsageContext.WHERE:
            bucket["where_count"] += 1
            if cit.expression and _EQUALITY_RE.search(cit.expression):
                bucket["where_equality_count"] += 1
        elif ctx is ColumnUsageContext.GROUP_BY:
            bucket["group_by_count"] += 1
        elif ctx is ColumnUsageContext.ORDER_BY:
            bucket["order_by_count"] += 1
        elif ctx is ColumnUsageContext.PARTITION_BY:
            bucket["partition_by_count"] += 1
        elif ctx is ColumnUsageContext.PROJECTION:
            bucket["projection_count"] += 1
    if citations:
        ck = consumer_ref.key()
        if ck not in bucket["_consumer_keys"]:
            bucket["_consumer_keys"].add(ck)
            bucket["consumers"].append(consumer_ref)


def _fold_joins(
    join_partner_bucket: dict[str, set[tuple[tuple, str]]],
    joins: list[JoinUsage],
    per_col: dict[str, dict],
) -> None:
    for j in joins:
        for pair in j.pairs:
            tcol = pair.target_column.lower()
            other = pair.other_column
            if tcol in join_partner_bucket:
                join_partner_bucket[tcol].add((j.other.key(), other))


def _unkey_ref(key: tuple) -> ObjectRef:
    return ObjectRef(database=key[0], schema=key[1] or "dbo", name=key[2] or "?")


def _render_sql(
    recs: list[TableRecommendation],
    platform: SqlTargetPlatform,
    ts: str,
) -> str:
    lines: list[str] = [
        f"-- recommend-index-stats generated {ts}",
        f"-- target platform: {platform.value}",
        f"-- {len(recs)} table(s) analysed",
        "--",
    ]
    for r in recs:
        lines.extend(_sql_block_for_table(r, platform))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _sql_block_for_table(r: TableRecommendation, platform: SqlTargetPlatform) -> list[str]:
    t = r.profile.table
    q = t.qualified()
    header = [
        "-- " + ("=" * 60),
        f"-- Table: {q}",
        f"-- Consumers analysed: {r.profile.total_consumers}"
        + (f" | rows: {r.profile.row_count}" if r.profile.row_count else "")
        + (f" | size_mb: {r.profile.size_mb:.1f}" if r.profile.size_mb else ""),
        "-- " + ("=" * 60),
    ]
    body: list[str] = list(header)
    if r.distribution is not None:
        body.extend(_sql_distribution(r))
    if r.cluster_by is not None:
        body.extend(_sql_cluster_by(r))
    for idx in r.indexes:
        body.extend(_sql_index(idx, platform))
    for stat in r.statistics:
        body.extend(_sql_statistic(stat, platform))
    if (
        not r.indexes
        and not r.statistics
        and r.distribution is None
        and r.cluster_by is None
    ):
        body.append("-- no recommendations for this table")
    if r.skipped_because_existing:
        body.append("-- skipped (existing objects cover the candidate):")
        for s in r.skipped_because_existing:
            body.append(f"--   * {s}")
    return body


def _sql_distribution(r: TableRecommendation) -> list[str]:
    d = r.distribution
    if d is None:
        return []
    t = r.profile.table
    q = t.qualified()
    kind = d.kind.value
    guess = "true" if d.is_guess else "false"
    dist_clause = (
        f"HASH([{d.hash_column}])"
        if d.kind is SynapseDistributionKind.HASH and d.hash_column
        else kind
    )
    out = [
        f"-- [{d.impact.value}] Distribution recommendation: {dist_clause}  |  guess={guess}",
        f"-- Rationale: {d.rationale}",
    ]
    if d.driving_citations:
        out.append(
            "-- Driven by: "
            + ", ".join(c.qualified() for c in d.driving_citations[:5])
            + (" ..." if len(d.driving_citations) > 5 else "")
        )
    out.append(
        "-- Changing distribution requires CTAS; DDL cannot ALTER it. Template:"
    )
    out.append(
        f"-- CREATE TABLE {t.schema_name}.{t.name}_new"
    )
    out.append(
        f"-- WITH ( DISTRIBUTION = {dist_clause}"
        + (", CLUSTERED COLUMNSTORE INDEX"
           if any(i.kind.value == "CLUSTERED_COLUMNSTORE" for i in r.indexes) else "")
        + " )"
    )
    out.append(f"-- AS SELECT * FROM {q};")
    if d.verification_queries:
        out.append("-- --- verification queries (run before applying) ---")
        for i, vq in enumerate(d.verification_queries, start=1):
            out.append(f"-- query {i}/{len(d.verification_queries)}:")
            for line in vq.splitlines():
                out.append(f"--   {line}")
    return out


def _sql_cluster_by(r: TableRecommendation) -> list[str]:
    cb = r.cluster_by
    if cb is None:
        return []
    t = r.profile.table
    q = t.qualified()
    cols_bracketed = ", ".join(f"[{c}]" for c in cb.columns)
    guess = "true" if cb.is_guess else "false"
    out = [
        f"-- [{cb.impact.value}] Fabric CLUSTER BY recommendation on ({', '.join(cb.columns)})  |  guess={guess}",
        f"-- Rationale: {cb.rationale}",
    ]
    if cb.driving_citations:
        out.append(
            "-- Driven by: "
            + ", ".join(c.qualified() for c in cb.driving_citations[:5])
            + (" ..." if len(cb.driving_citations) > 5 else "")
        )
    if cb.excluded_columns:
        out.append("-- Columns excluded from CLUSTER BY (unsupported types):")
        for name, reason in cb.excluded_columns:
            out.append(f"--   * [{name}] — {reason}")
    out.append(
        "-- CLUSTER BY cannot be added via ALTER; recreate the table using CTAS:"
    )
    out.append(f"-- CREATE TABLE {t.schema_name}.{t.name}_new")
    out.append(f"-- WITH (CLUSTER BY ({cols_bracketed}))")
    out.append(f"-- AS SELECT * FROM {q};")
    if cb.verification_queries:
        out.append("-- --- verification queries (run before applying) ---")
        for i, vq in enumerate(cb.verification_queries, start=1):
            out.append(f"-- query {i}/{len(cb.verification_queries)}:")
            for line in vq.splitlines():
                out.append(f"--   {line}")
    return out


def _sql_index(idx: IndexRecommendation, platform: SqlTargetPlatform) -> list[str]:
    t = idx.table
    q = t.qualified()
    name = _index_name(idx)
    header = [
        f"-- [{idx.impact.value}] {idx.kind.value} on {q}"
        + (f" ({', '.join(idx.key_columns)})" if idx.key_columns else ""),
        f"-- Rationale: {idx.rationale}",
    ]
    if idx.driving_citations:
        header.append(
            "-- Driven by: "
            + ", ".join(c.qualified() for c in idx.driving_citations[:5])
            + (" ..." if len(idx.driving_citations) > 5 else "")
        )
    match idx.kind:
        case IndexKind.CLUSTERED_COLUMNSTORE:
            stmt = f"CREATE CLUSTERED COLUMNSTORE INDEX {name} ON {q};"
        case IndexKind.NONCLUSTERED_COLUMNSTORE:
            cols = f" ({', '.join(f'[{c}]' for c in idx.key_columns)})" if idx.key_columns else ""
            stmt = f"CREATE NONCLUSTERED COLUMNSTORE INDEX {name} ON {q}{cols};"
        case IndexKind.CLUSTERED:
            key = ", ".join(f"[{c}]" for c in idx.key_columns)
            stmt = f"CREATE CLUSTERED INDEX {name} ON {q} ({key});"
        case IndexKind.NONCLUSTERED:
            key = ", ".join(f"[{c}]" for c in idx.key_columns)
            include = (
                f" INCLUDE ({', '.join(f'[{c}]' for c in idx.include_columns)})"
                if idx.include_columns and platform.supports_included_columns
                else ""
            )
            filt = (
                f" WHERE {idx.filter_predicate}"
                if idx.filter_predicate and platform.supports_filtered_index
                else ""
            )
            stmt = f"CREATE NONCLUSTERED INDEX {name} ON {q} ({key}){include}{filt};"
        case _:
            stmt = f"-- (unsupported index kind: {idx.kind.value})"
    return header + [stmt]


def _sql_statistic(stat: StatisticRecommendation, platform: SqlTargetPlatform) -> list[str]:
    t = stat.table
    q = t.qualified()
    name = _stat_name(stat)
    cols = ", ".join(f"[{c}]" for c in stat.columns)
    filt = (
        f" WHERE {stat.filter_predicate}"
        if stat.filter_predicate and platform.supports_filtered_index
        else ""
    )
    # Fabric Warehouse requires FULLSCAN on CREATE STATISTICS. Synapse Dedicated
    # Pool also benefits from an explicit scan on load. Other platforms accept
    # default sampling but FULLSCAN is safe and deterministic.
    trailing = " WITH FULLSCAN" if platform in {
        SqlTargetPlatform.FABRIC_WAREHOUSE,
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
    } else ""
    return [
        f"-- [{stat.impact.value}] {stat.kind.value} statistic on {q} ({', '.join(stat.columns)})",
        f"-- Rationale: {stat.rationale}",
        f"CREATE STATISTICS {name} ON {q} ({cols}){filt}{trailing};",
    ]


def _index_name(idx: IndexRecommendation) -> str:
    t = idx.table
    key_slug = "_".join(idx.key_columns) if idx.key_columns else "cci"
    return f"IX_{t.schema_name}_{t.name}_{key_slug}"[:120]


def _stat_name(stat: StatisticRecommendation) -> str:
    t = stat.table
    return f"stat_{t.schema_name}_{t.name}_{'_'.join(stat.columns)}"[:120]


def _render_markdown(
    recs: list[TableRecommendation],
    platform: SqlTargetPlatform,
    ts: str,
) -> str:
    lines: list[str] = [
        f"# Index & Statistics Recommendations",
        "",
        f"- Generated: `{ts}`",
        f"- Target platform: **{platform.value}**",
        f"- Tables analysed: **{len(recs)}**",
        f"- Total indexes recommended: **{sum(len(r.indexes) for r in recs)}**",
        f"- Total statistics recommended: **{sum(len(r.statistics) for r in recs)}**",
        "",
    ]
    for r in recs:
        lines.extend(_md_block_for_table(r, platform))
    return "\n".join(lines).rstrip() + "\n"


def _md_block_for_table(r: TableRecommendation, platform: SqlTargetPlatform) -> list[str]:
    t = r.profile.table
    header = [
        f"## {t.qualified()}",
        "",
        f"- Consumers analysed: **{r.profile.total_consumers}**"
        + (f" | rows: `{r.profile.row_count}`" if r.profile.row_count else "")
        + (f" | size: `{r.profile.size_mb:.1f} MB`" if r.profile.size_mb else ""),
        "",
    ]
    if r.distribution is not None:
        header.extend(_md_distribution(r))
    if r.cluster_by is not None:
        header.extend(_md_cluster_by(r))
    if r.indexes:
        header.append("### Indexes")
        header.append("")
        header.append("| Impact | Kind | Key columns | Include | Rationale |")
        header.append("| --- | --- | --- | --- | --- |")
        for idx in r.indexes:
            header.append(
                f"| {idx.impact.value} | {idx.kind.value} | "
                f"{', '.join(idx.key_columns) or '(table-level)'} | "
                f"{', '.join(idx.include_columns) or '-'} | {idx.rationale} |"
            )
        header.append("")
    if r.statistics:
        header.append("### Statistics")
        header.append("")
        header.append("| Impact | Kind | Columns | Rationale |")
        header.append("| --- | --- | --- | --- |")
        for s in r.statistics:
            header.append(
                f"| {s.impact.value} | {s.kind.value} | "
                f"{', '.join(s.columns)} | {s.rationale} |"
            )
        header.append("")
    if r.skipped_because_existing:
        header.append("### Skipped (already covered)")
        header.append("")
        for s in r.skipped_because_existing:
            header.append(f"- {s}")
        header.append("")
    return header


def _md_cluster_by(r: TableRecommendation) -> list[str]:
    cb = r.cluster_by
    if cb is None:
        return []
    lines = [
        "### CLUSTER BY (Fabric Warehouse)",
        "",
        f"- **Columns:** `{', '.join(cb.columns)}`",
        f"- **Impact:** {cb.impact.value}",
        f"- **Guess:** `{cb.is_guess}`",
        f"- **Rationale:** {cb.rationale}",
    ]
    if cb.driving_citations:
        cits = ", ".join(c.qualified() for c in cb.driving_citations[:5])
        lines.append(f"- **Driven by:** {cits}")
    if cb.excluded_columns:
        lines.append("- **Excluded (unsupported types):**")
        for name, reason in cb.excluded_columns:
            lines.append(f"  - `{name}` — {reason}")
    lines.append("")
    lines.append("> CLUSTER BY cannot be added via ALTER; use CTAS to recreate.")
    lines.append("")
    if cb.verification_queries:
        lines.append("**Verification queries — run before applying:**")
        lines.append("")
        for vq in cb.verification_queries:
            lines.append("```sql")
            lines.append(vq)
            lines.append("```")
            lines.append("")
    return lines


def _md_distribution(r: TableRecommendation) -> list[str]:
    d = r.distribution
    if d is None:
        return []
    lines = [
        "### Distribution",
        "",
        f"- **Kind:** `{d.kind.value}`"
        + (f" on column `{d.hash_column}`" if d.hash_column else ""),
        f"- **Impact:** {d.impact.value}",
        f"- **Guess:** `{d.is_guess}`",
        f"- **Rationale:** {d.rationale}",
    ]
    if d.driving_citations:
        cits = ", ".join(c.qualified() for c in d.driving_citations[:5])
        lines.append(f"- **Driven by:** {cits}")
    if d.verification_queries:
        lines.append("")
        lines.append("**Verification queries — run before applying:**")
        lines.append("")
        for vq in d.verification_queries:
            lines.append("```sql")
            lines.append(vq)
            lines.append("```")
            lines.append("")
    lines.append("")
    return lines
