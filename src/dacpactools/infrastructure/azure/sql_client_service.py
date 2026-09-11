from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from dacpactools.application.ports._handles import (
    AccessToken,
    ConnectionHandle,
    LiveConnection,
)
from dacpactools.domain.enums import (
    IndexKind,
    ObjectType,
    SqlTargetPlatform,
    StatisticKind,
    SynapseDistributionKind,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.domain.physical_design import ExistingIndex, ExistingStatistic
from dacpactools.presentation.logging_config import get_logger

_SQL_TYPE_TO_OBJECT_TYPE: dict[str, ObjectType] = {
    "U": ObjectType.TABLE,
    "V": ObjectType.VIEW,
    "P": ObjectType.PROCEDURE,
    "FN": ObjectType.SCALAR_FUNCTION,
    "IF": ObjectType.TABLE_VALUED_FUNCTION,
    "TF": ObjectType.TABLE_VALUED_FUNCTION,
    "FT": ObjectType.TABLE_VALUED_FUNCTION,
    "SN": ObjectType.SYNONYM,
    "ET": ObjectType.EXTERNAL_TABLE,
}


_INDEX_TYPE_TO_KIND: dict[int, IndexKind] = {
    1: IndexKind.CLUSTERED,
    2: IndexKind.NONCLUSTERED,
    5: IndexKind.CLUSTERED_COLUMNSTORE,
    6: IndexKind.NONCLUSTERED_COLUMNSTORE,
}


_DISTRIBUTION_POLICY_TO_KIND: dict[str, SynapseDistributionKind] = {
    "HASH": SynapseDistributionKind.HASH,
    "ROUND_ROBIN": SynapseDistributionKind.ROUND_ROBIN,
    "REPLICATE": SynapseDistributionKind.REPLICATE,
    "REPLICATED": SynapseDistributionKind.REPLICATE,
}


log = get_logger("sqlclient")


def _clr_types() -> tuple[Any, Any]:
    from Microsoft.Data.SqlClient import SqlConnection  # type: ignore
    from System.Data import CommandBehavior  # type: ignore

    return SqlConnection, CommandBehavior


class SqlClientMetadataService:
    """Live-DB metadata queries via Microsoft.Data.SqlClient with AccessToken."""

    def open(self, connection: LiveConnection, access_token: AccessToken) -> ConnectionHandle:
        SqlConnection, _ = _clr_types()
        conn = SqlConnection(connection.connection_string())
        conn.AccessToken = access_token.token
        conn.Open()
        log.info("sql connection opened", server=connection.server, database=connection.database)
        return ConnectionHandle(connection=conn, live=connection)

    def fetch_object_definition(self, conn: ConnectionHandle, ref: ObjectRef) -> str:
        _, CommandBehavior = _clr_types()
        sql = (
            "SELECT m.definition "
            "FROM sys.sql_modules m "
            "JOIN sys.objects o ON m.object_id = o.object_id "
            "JOIN sys.schemas s ON o.schema_id = s.schema_id "
            "WHERE s.name = @schema AND o.name = @name"
        )
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = sql
        cmd.Parameters.AddWithValue("@schema", ref.schema_name)
        cmd.Parameters.AddWithValue("@name", ref.name)
        reader = cmd.ExecuteReader(CommandBehavior.SingleRow)
        try:
            if not reader.Read():
                return ""
            return str(reader.GetString(0))
        finally:
            reader.Close()

    def fetch_columns(self, conn: ConnectionHandle, ref: ObjectRef) -> tuple[ColumnRef, ...]:
        sql = (
            "SELECT c.name, c.column_id, TYPE_NAME(c.user_type_id) "
            "FROM sys.columns c "
            "JOIN sys.objects o ON c.object_id = o.object_id "
            "JOIN sys.schemas s ON o.schema_id = s.schema_id "
            "WHERE s.name = @schema AND o.name = @name "
            "ORDER BY c.column_id"
        )
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = sql
        cmd.Parameters.AddWithValue("@schema", ref.schema_name)
        cmd.Parameters.AddWithValue("@name", ref.name)
        reader = cmd.ExecuteReader()
        out: list[ColumnRef] = []
        try:
            while reader.Read():
                out.append(
                    ColumnRef(
                        owner=ref,
                        column_name=str(reader.GetString(0)),
                        ordinal=int(reader.GetInt32(1)) - 1,
                        data_type=str(reader.GetString(2)) if not reader.IsDBNull(2) else None,
                    )
                )
        finally:
            reader.Close()
        return tuple(out)

    def list_all_objects(self, conn: ConnectionHandle) -> Iterable[ObjectRef]:
        sql = (
            "SELECT s.name, o.name, RTRIM(o.type) "
            "FROM sys.objects o "
            "JOIN sys.schemas s ON o.schema_id = s.schema_id "
            "WHERE o.is_ms_shipped = 0 "
            "AND o.parent_object_id = 0 "
            "AND RTRIM(o.type) IN ('U','V','P','FN','IF','TF','FT','SN','ET') "
            "ORDER BY s.name, o.name"
        )
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = sql
        reader = cmd.ExecuteReader()
        out: list[ObjectRef] = []
        try:
            while reader.Read():
                schema = str(reader.GetString(0))
                name = str(reader.GetString(1))
                type_code = str(reader.GetString(2)).strip()
                ot = _SQL_TYPE_TO_OBJECT_TYPE.get(type_code, ObjectType.UNKNOWN)
                out.append(
                    ObjectRef(database=None, schema=schema, name=name, object_type=ot)
                )
        finally:
            reader.Close()
        return tuple(out)

    def close(self, conn: ConnectionHandle) -> None:
        try:
            conn.connection.Close()
            conn.connection.Dispose()
        except Exception as e:  # noqa: BLE001
            log.warning("close failed", err=str(e))

    def detect_target_platform(self, conn: ConnectionHandle) -> SqlTargetPlatform:
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = "SELECT CAST(SERVERPROPERTY('EngineEdition') AS int)"
        try:
            raw = cmd.ExecuteScalar()
        except Exception as e:  # noqa: BLE001
            log.warning("EngineEdition query failed", err=str(e))
            return SqlTargetPlatform.UNKNOWN
        if raw is None:
            return SqlTargetPlatform.UNKNOWN
        try:
            edition = int(raw)
        except (TypeError, ValueError):
            return SqlTargetPlatform.UNKNOWN
        platform = SqlTargetPlatform.from_engine_edition(edition)
        # Fabric Warehouse shares EngineEdition with Synapse Serverless (11) or
        # in some builds with Synapse Dedicated (6). Secondary probe on the
        # database's ServiceObjective / EngineEdition combo disambiguates.
        if platform in {
            SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL,
            SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
        } and self._is_fabric_warehouse(conn):
            platform = SqlTargetPlatform.FABRIC_WAREHOUSE
        log.info("target platform detected", edition=edition, platform=platform.value)
        return platform

    def _is_fabric_warehouse(self, conn: ConnectionHandle) -> bool:
        probe = (
            "SELECT "
            "  ISNULL(CAST(DATABASEPROPERTYEX(DB_NAME(),'ServiceObjective') AS varchar(128)),''), "
            "  ISNULL(CAST(DATABASEPROPERTYEX(DB_NAME(),'Edition') AS varchar(128)),''), "
            "  ISNULL(CAST(SERVERPROPERTY('ProductVersion') AS varchar(64)),'')"
        )
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = probe
        try:
            reader = cmd.ExecuteReader()
        except Exception as e:  # noqa: BLE001
            log.debug("fabric probe failed", err=str(e))
            return False
        try:
            if not reader.Read():
                return False
            svc = str(reader.GetString(0)).lower()
            edition = str(reader.GetString(1)).lower()
            product = str(reader.GetString(2)).lower()
        finally:
            reader.Close()
        haystack = f"{svc}|{edition}|{product}"
        return "fabric" in haystack

    def fetch_indexes(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> tuple[ExistingIndex, ...]:
        sql = (
            "SELECT i.name, i.type, i.is_unique, i.has_filter, i.filter_definition, "
            "       i.index_id, c.name AS col_name, ic.key_ordinal, ic.is_included_column "
            "FROM sys.indexes i "
            "JOIN sys.objects o ON o.object_id = i.object_id "
            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
            "JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
            "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
            "WHERE s.name = @schema AND o.name = @name AND i.type <> 0 "
            "ORDER BY i.index_id, ic.key_ordinal, ic.index_column_id"
        )
        rows = self._execute_reader(
            conn, sql, [("@schema", ref.schema_name), ("@name", ref.name)]
        )
        grouped: dict[int, dict] = {}
        for r in rows:
            index_id = int(r[5])
            entry = grouped.setdefault(
                index_id,
                {
                    "name": str(r[0]) if r[0] is not None else f"idx_{index_id}",
                    "type": int(r[1]),
                    "is_unique": bool(r[2]),
                    "filter": str(r[4]) if r[3] and r[4] is not None else None,
                    "keys": [],
                    "includes": [],
                },
            )
            col_name = str(r[6])
            if bool(r[8]):
                entry["includes"].append(col_name)
            else:
                entry["keys"].append(col_name)
        out: list[ExistingIndex] = []
        for entry in grouped.values():
            kind = _INDEX_TYPE_TO_KIND.get(entry["type"], IndexKind.NONCLUSTERED)
            out.append(
                ExistingIndex(
                    table=ref,
                    name=entry["name"],
                    kind=kind,
                    key_columns=tuple(entry["keys"]),
                    include_columns=tuple(entry["includes"]),
                    filter_predicate=entry["filter"],
                    is_unique=entry["is_unique"],
                )
            )
        return tuple(out)

    def fetch_statistics(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> tuple[ExistingStatistic, ...]:
        sql = (
            "SELECT st.name, st.stats_id, st.has_filter, st.filter_definition, "
            "       c.name AS col_name, sc.stats_column_id "
            "FROM sys.stats st "
            "JOIN sys.objects o ON o.object_id = st.object_id "
            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
            "JOIN sys.stats_columns sc ON sc.object_id = st.object_id AND sc.stats_id = st.stats_id "
            "JOIN sys.columns c ON c.object_id = sc.object_id AND c.column_id = sc.column_id "
            "WHERE s.name = @schema AND o.name = @name AND st.user_created = 1 "
            "ORDER BY st.stats_id, sc.stats_column_id"
        )
        rows = self._execute_reader(
            conn, sql, [("@schema", ref.schema_name), ("@name", ref.name)]
        )
        grouped: dict[int, dict] = {}
        for r in rows:
            stats_id = int(r[1])
            entry = grouped.setdefault(
                stats_id,
                {
                    "name": str(r[0]) if r[0] is not None else f"stat_{stats_id}",
                    "filter": str(r[3]) if r[2] and r[3] is not None else None,
                    "cols": [],
                },
            )
            entry["cols"].append(str(r[4]))
        out: list[ExistingStatistic] = []
        for entry in grouped.values():
            cols = tuple(entry["cols"])
            kind = (
                StatisticKind.FILTERED
                if entry["filter"]
                else (StatisticKind.MULTI_COLUMN if len(cols) > 1 else StatisticKind.SINGLE_COLUMN)
            )
            out.append(
                ExistingStatistic(
                    table=ref,
                    name=entry["name"],
                    kind=kind,
                    columns=cols,
                    filter_predicate=entry["filter"],
                )
            )
        return tuple(out)

    def fetch_row_count(self, conn: ConnectionHandle, ref: ObjectRef) -> int | None:
        sql = (
            "SELECT SUM(ps.row_count) "
            "FROM sys.dm_db_partition_stats ps "
            "JOIN sys.objects o ON o.object_id = ps.object_id "
            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
            "WHERE s.name = @schema AND o.name = @name AND ps.index_id IN (0,1)"
        )
        return _scalar_int(
            self._execute_scalar(
                conn, sql, [("@schema", ref.schema_name), ("@name", ref.name)]
            )
        )

    def fetch_synapse_distribution(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> tuple[SynapseDistributionKind, tuple[str, ...]] | None:
        sql = (
            "SELECT dt.distribution_policy_desc, c.name "
            "FROM sys.pdw_table_distribution_properties dt "
            "JOIN sys.objects o ON o.object_id = dt.object_id "
            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
            "LEFT JOIN sys.pdw_column_distribution_properties cd "
            "    ON cd.object_id = dt.object_id AND cd.distribution_ordinal > 0 "
            "LEFT JOIN sys.columns c "
            "    ON c.object_id = cd.object_id AND c.column_id = cd.column_id "
            "WHERE s.name = @schema AND o.name = @name "
            "ORDER BY cd.distribution_ordinal"
        )
        try:
            rows = self._execute_reader(
                conn, sql, [("@schema", ref.schema_name), ("@name", ref.name)]
            )
        except Exception as e:  # noqa: BLE001
            log.debug("synapse distribution query failed (non-synapse target?)", err=str(e))
            return None
        if not rows:
            return None
        raw_policy = (str(rows[0][0]) if rows[0][0] is not None else "").upper()
        kind = _DISTRIBUTION_POLICY_TO_KIND.get(raw_policy, SynapseDistributionKind.UNKNOWN)
        cols = tuple(str(r[1]) for r in rows if r[1] is not None)
        return kind, cols

    def fetch_table_size_mb(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> float | None:
        sql_synapse = (
            "SELECT SUM(nps.reserved_page_count) * 8.0 / 1024 "
            "FROM sys.dm_pdw_nodes_db_partition_stats nps "
            "JOIN sys.pdw_nodes_tables nt "
            "    ON nps.object_id = nt.object_id AND nps.pdw_node_id = nt.pdw_node_id "
            "JOIN sys.pdw_table_mappings tm ON nt.name = tm.physical_name "
            "JOIN sys.objects o ON tm.object_id = o.object_id "
            "JOIN sys.schemas s ON o.schema_id = s.schema_id "
            "WHERE s.name = @schema AND o.name = @name"
        )
        try:
            val = self._execute_scalar(
                conn, sql_synapse, [("@schema", ref.schema_name), ("@name", ref.name)]
            )
        except Exception:  # noqa: BLE001
            val = None
        if val is not None:
            return _scalar_float(val)
        sql_fallback = (
            "SELECT SUM(a.total_pages) * 8.0 / 1024 "
            "FROM sys.allocation_units a "
            "JOIN sys.partitions p ON p.partition_id = a.container_id "
            "JOIN sys.objects o ON o.object_id = p.object_id "
            "JOIN sys.schemas s ON s.schema_id = o.schema_id "
            "WHERE s.name = @schema AND o.name = @name"
        )
        try:
            val = self._execute_scalar(
                conn, sql_fallback, [("@schema", ref.schema_name), ("@name", ref.name)]
            )
        except Exception as e:  # noqa: BLE001
            log.debug("table size query failed", err=str(e))
            return None
        return _scalar_float(val)

    def fetch_column_distinct_count(
        self, conn: ConnectionHandle, ref: ObjectRef, column: str
    ) -> int | None:
        qualified = f"[{ref.schema_name}].[{ref.name}]"
        sql = (
            f"SELECT COUNT_BIG(DISTINCT [{column}]) FROM {qualified}"
        )
        try:
            val = self._execute_scalar(conn, sql, [])
        except Exception as e:  # noqa: BLE001
            log.debug("distinct count query failed", err=str(e), column=column)
            return None
        return _scalar_int(val)

    def fetch_distribution_skew_ratio(
        self, conn: ConnectionHandle, ref: ObjectRef
    ) -> float | None:
        sql = (
            "SELECT MAX(row_count) * 1.0 / NULLIF(AVG(row_count * 1.0), 0) "
            "FROM ( "
            "    SELECT pdw_node_id, distribution_id, SUM(row_count) AS row_count "
            "    FROM sys.dm_pdw_nodes_db_partition_stats nps "
            "    JOIN sys.pdw_nodes_tables nt "
            "        ON nps.object_id = nt.object_id AND nps.pdw_node_id = nt.pdw_node_id "
            "    JOIN sys.pdw_table_mappings tm ON nt.name = tm.physical_name "
            "    JOIN sys.objects o ON tm.object_id = o.object_id "
            "    JOIN sys.schemas s ON o.schema_id = s.schema_id "
            "    WHERE s.name = @schema AND o.name = @name "
            "    GROUP BY pdw_node_id, distribution_id "
            ") d"
        )
        try:
            val = self._execute_scalar(
                conn, sql, [("@schema", ref.schema_name), ("@name", ref.name)]
            )
        except Exception as e:  # noqa: BLE001
            log.debug("skew query failed (non-synapse?)", err=str(e))
            return None
        return _scalar_float(val)

    def _execute_reader(
        self, conn: ConnectionHandle, sql: str, params: list[tuple[str, Any]]
    ) -> list[tuple]:
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = sql
        for name, value in params:
            cmd.Parameters.AddWithValue(name, value)
        reader = cmd.ExecuteReader()
        rows: list[tuple] = []
        try:
            field_count = int(reader.FieldCount)
            while reader.Read():
                row = tuple(
                    None if reader.IsDBNull(i) else reader.GetValue(i)
                    for i in range(field_count)
                )
                rows.append(row)
        finally:
            reader.Close()
        return rows

    def _execute_scalar(
        self, conn: ConnectionHandle, sql: str, params: list[tuple[str, Any]]
    ) -> Any:
        cmd = conn.connection.CreateCommand()
        cmd.CommandText = sql
        for name, value in params:
            cmd.Parameters.AddWithValue(name, value)
        return cmd.ExecuteScalar()


def _scalar_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _scalar_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
