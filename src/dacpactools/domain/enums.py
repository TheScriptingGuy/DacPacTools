from __future__ import annotations

from enum import Enum


class ObjectType(str, Enum):
    PROCEDURE = "PROCEDURE"
    VIEW = "VIEW"
    TABLE_VALUED_FUNCTION = "TABLE_VALUED_FUNCTION"
    SCALAR_FUNCTION = "SCALAR_FUNCTION"
    TABLE = "TABLE"
    SYNONYM = "SYNONYM"
    EXTERNAL_TABLE = "EXTERNAL_TABLE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_leaf(self) -> bool:
        return self in {ObjectType.TABLE, ObjectType.EXTERNAL_TABLE}

    @property
    def has_body(self) -> bool:
        return self in {
            ObjectType.PROCEDURE,
            ObjectType.VIEW,
            ObjectType.TABLE_VALUED_FUNCTION,
            ObjectType.SCALAR_FUNCTION,
        }


class EdgeKind(str, Enum):
    REFERENCES = "REFERENCES"
    REFERENCED_BY = "REFERENCED_BY"
    COLUMN_FLOW = "COLUMN_FLOW"


class ColumnUsageKind(str, Enum):
    JOIN_ON = "JOIN_ON"
    WHERE = "WHERE"
    GROUP_BY = "GROUP_BY"
    HAVING = "HAVING"
    ORDER_BY = "ORDER_BY"
    PARTITION_BY = "PARTITION_BY"


class SynapseDistributionKind(str, Enum):
    HASH = "HASH"
    ROUND_ROBIN = "ROUND_ROBIN"
    REPLICATE = "REPLICATE"
    UNKNOWN = "UNKNOWN"


class SynapseIndexKind(str, Enum):
    CLUSTERED_COLUMNSTORE = "CLUSTERED_COLUMNSTORE"
    CLUSTERED = "CLUSTERED"
    HEAP = "HEAP"
    NONE = "NONE"


class LineageDirection(str, Enum):
    UPSTREAM = "UPSTREAM"
    DOWNSTREAM = "DOWNSTREAM"


class ActivityType(str, Enum):
    SQL_STORED_PROCEDURE = "SQL_STORED_PROCEDURE"
    SQL_SCRIPT = "SQL_SCRIPT"
    SQL_LOOKUP = "SQL_LOOKUP"
    SQL_COPY_SOURCE = "SQL_COPY_SOURCE"
    SQL_QUERY = "SQL_QUERY"
    UNSUPPORTED = "UNSUPPORTED"


class OrchestratorKind(str, Enum):
    ADF = "ADF"
    AIRFLOW = "AIRFLOW"


class SqlTargetPlatform(str, Enum):
    SQL_SERVER = "SQL_SERVER"
    AZURE_SQL_DB = "AZURE_SQL_DB"
    AZURE_SQL_MANAGED_INSTANCE = "AZURE_SQL_MANAGED_INSTANCE"
    SYNAPSE_DEDICATED_POOL = "SYNAPSE_DEDICATED_POOL"
    SYNAPSE_SERVERLESS_POOL = "SYNAPSE_SERVERLESS_POOL"
    FABRIC_WAREHOUSE = "FABRIC_WAREHOUSE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_mpp(self) -> bool:
        return self is SqlTargetPlatform.SYNAPSE_DEDICATED_POOL

    @property
    def is_serverless(self) -> bool:
        return self is SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL

    @property
    def is_fabric(self) -> bool:
        return self is SqlTargetPlatform.FABRIC_WAREHOUSE

    @property
    def is_stats_only(self) -> bool:
        return self in {
            SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL,
            SqlTargetPlatform.FABRIC_WAREHOUSE,
        }

    @property
    def supports_persistent_indexes(self) -> bool:
        return self not in {
            SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL,
            SqlTargetPlatform.FABRIC_WAREHOUSE,
            SqlTargetPlatform.UNKNOWN,
        }

    @property
    def supports_included_columns(self) -> bool:
        return self in {
            SqlTargetPlatform.SQL_SERVER,
            SqlTargetPlatform.AZURE_SQL_DB,
            SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE,
        }

    @property
    def supports_filtered_index(self) -> bool:
        return self.supports_included_columns

    @property
    def supports_columnstore(self) -> bool:
        return self in {
            SqlTargetPlatform.SQL_SERVER,
            SqlTargetPlatform.AZURE_SQL_DB,
            SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE,
            SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
        }

    @property
    def supports_fullscan_stats(self) -> bool:
        """Fabric requires WITH FULLSCAN; classic engines accept it but default is sample."""
        return True

    @classmethod
    def from_engine_edition(cls, engine_edition: int | None) -> SqlTargetPlatform:
        return _ENGINE_EDITION_MAP.get(engine_edition or -1, cls.UNKNOWN)


_ENGINE_EDITION_MAP: dict[int, SqlTargetPlatform] = {
    2: SqlTargetPlatform.SQL_SERVER,
    3: SqlTargetPlatform.SQL_SERVER,
    4: SqlTargetPlatform.SQL_SERVER,
    5: SqlTargetPlatform.AZURE_SQL_DB,
    6: SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
    8: SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE,
    9: SqlTargetPlatform.SQL_SERVER,
    11: SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL,
}


class IndexKind(str, Enum):
    CLUSTERED_COLUMNSTORE = "CLUSTERED_COLUMNSTORE"
    NONCLUSTERED_COLUMNSTORE = "NONCLUSTERED_COLUMNSTORE"
    CLUSTERED = "CLUSTERED"
    NONCLUSTERED = "NONCLUSTERED"
    HEAP = "HEAP"


class StatisticKind(str, Enum):
    SINGLE_COLUMN = "SINGLE_COLUMN"
    MULTI_COLUMN = "MULTI_COLUMN"
    FILTERED = "FILTERED"


class RecommendationImpact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
