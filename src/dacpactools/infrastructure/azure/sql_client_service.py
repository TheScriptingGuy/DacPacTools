from __future__ import annotations

from typing import Any

from dacpactools.application.ports._handles import (
    AccessToken,
    ConnectionHandle,
    LiveConnection,
)
from dacpactools.domain.identifiers import ColumnRef, ObjectRef
from dacpactools.presentation.logging_config import get_logger

log = get_logger("sqlclient")


def _clr_types() -> tuple[Any, Any]:
    from Microsoft.Data.SqlClient import SqlConnection  # type: ignore  # noqa: PLC0415
    from System.Data import CommandBehavior  # type: ignore  # noqa: PLC0415

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

    def close(self, conn: ConnectionHandle) -> None:
        try:
            conn.connection.Close()
            conn.connection.Dispose()
        except Exception as e:  # noqa: BLE001
            log.warning("close failed", err=str(e))
