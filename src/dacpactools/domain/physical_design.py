from __future__ import annotations

from dataclasses import dataclass, field

from dacpactools.domain.enums import (
    IndexKind,
    RecommendationImpact,
    StatisticKind,
    SynapseDistributionKind,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


@dataclass(frozen=True)
class ColumnUsageProfile:
    column: ColumnRef
    join_on_count: int = 0
    where_count: int = 0
    where_equality_count: int = 0
    group_by_count: int = 0
    order_by_count: int = 0
    partition_by_count: int = 0
    projection_count: int = 0
    join_partners: tuple[tuple[ObjectRef, str], ...] = ()
    consumers: tuple[ObjectRef, ...] = ()


@dataclass(frozen=True)
class TableUsageProfile:
    table: ObjectRef
    columns: tuple[ColumnUsageProfile, ...] = ()
    total_consumers: int = 0
    row_count: int | None = None
    size_mb: float | None = None
    is_staging_name: bool = False


@dataclass(frozen=True)
class ColumnLiveMetric:
    column_name: str
    distinct_count: int | None = None
    skew_ratio: float | None = None


@dataclass(frozen=True)
class ExistingIndex:
    table: ObjectRef
    name: str
    kind: IndexKind
    key_columns: tuple[str, ...]
    include_columns: tuple[str, ...] = ()
    filter_predicate: str | None = None
    is_unique: bool = False


@dataclass(frozen=True)
class ExistingStatistic:
    table: ObjectRef
    name: str
    kind: StatisticKind
    columns: tuple[str, ...]
    filter_predicate: str | None = None


@dataclass(frozen=True)
class IndexRecommendation:
    table: ObjectRef
    kind: IndexKind
    key_columns: tuple[str, ...] = ()
    include_columns: tuple[str, ...] = ()
    filter_predicate: str | None = None
    impact: RecommendationImpact = RecommendationImpact.MEDIUM
    rationale: str = ""
    driving_citations: tuple[ObjectRef, ...] = ()


@dataclass(frozen=True)
class StatisticRecommendation:
    table: ObjectRef
    kind: StatisticKind
    columns: tuple[str, ...]
    filter_predicate: str | None = None
    impact: RecommendationImpact = RecommendationImpact.MEDIUM
    rationale: str = ""
    driving_citations: tuple[ObjectRef, ...] = ()


@dataclass(frozen=True)
class ClusterByRecommendation:
    """Fabric Warehouse `WITH (CLUSTER BY (...))` recommendation. Max 4 columns.
    Cannot be applied via ALTER — must be set at table creation (CTAS)."""
    table: ObjectRef
    columns: tuple[str, ...]
    impact: RecommendationImpact = RecommendationImpact.MEDIUM
    rationale: str = ""
    driving_citations: tuple[ObjectRef, ...] = ()
    verification_queries: tuple[str, ...] = ()
    is_guess: bool = False
    excluded_columns: tuple[tuple[str, str], ...] = ()  # (column_name, reason)


@dataclass(frozen=True)
class DistributionRecommendation:
    table: ObjectRef
    kind: SynapseDistributionKind
    hash_column: str | None = None
    impact: RecommendationImpact = RecommendationImpact.MEDIUM
    rationale: str = ""
    driving_citations: tuple[ObjectRef, ...] = ()
    verification_queries: tuple[str, ...] = ()
    is_guess: bool = False


@dataclass(frozen=True)
class TableRecommendation:
    profile: TableUsageProfile
    distribution: DistributionRecommendation | None = None
    cluster_by: ClusterByRecommendation | None = None
    indexes: tuple[IndexRecommendation, ...] = ()
    statistics: tuple[StatisticRecommendation, ...] = ()
    skipped_because_existing: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = ()
