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
