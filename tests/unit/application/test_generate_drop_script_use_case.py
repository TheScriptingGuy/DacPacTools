from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from dacpactools.application.dto.drop_script import DropScriptRequest
from dacpactools.application.ports._handles import (
    AccessToken,
    ConnectionHandle,
    DacPacHandle,
    LiveConnection,
    TSqlObjectHandle,
)
from dacpactools.application.use_cases.generate_drop_script import (
    GenerateDropScriptUseCase,
)
from dacpactools.domain.enums import ObjectType
from dacpactools.domain.identifiers import ColumnRef, ObjectRef


def _ref(schema: str, name: str, ot: ObjectType) -> ObjectRef:
    return ObjectRef(database=None, schema=schema, name=name, object_type=ot)


class FakeDacPacService:
    def __init__(self, objects: list[ObjectRef]) -> None:
        self._objects = objects

    def load(self, dacpac_path: Path) -> DacPacHandle:
        return DacPacHandle(model=None, source_path=str(dacpac_path))

    def load_from_live(self, connection: LiveConnection, tmp_dir: Path) -> DacPacHandle:
        return DacPacHandle(model=None, source_path=None)

    def list_all_objects(self, handle: DacPacHandle) -> Iterable[TSqlObjectHandle]:
        for r in self._objects:
            yield TSqlObjectHandle(ref=r, tsql_object=None)

    def find_object(self, handle: DacPacHandle, ref: ObjectRef) -> TSqlObjectHandle | None:
        return None

    def get_referenced(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]:
        return []

    def get_referencing(self, obj: TSqlObjectHandle) -> Iterable[TSqlObjectHandle]:
        return []

    def get_body_script(self, obj: TSqlObjectHandle) -> str | None:
        return None

    def get_columns(self, obj: TSqlObjectHandle) -> tuple[ColumnRef, ...]:
        return ()

    def resolve_synonym_target(self, obj: TSqlObjectHandle) -> ObjectRef | None:
        return None


class FakeSqlMetadataService:
    def __init__(self, objects: list[ObjectRef]) -> None:
        self._objects = objects
        self.opened = 0
        self.closed = 0

    def open(self, connection: LiveConnection, access_token: AccessToken) -> ConnectionHandle:
        self.opened += 1
        return ConnectionHandle(connection=None, live=connection)

    def fetch_object_definition(self, conn: ConnectionHandle, ref: ObjectRef) -> str:
        return ""

    def fetch_columns(self, conn: ConnectionHandle, ref: ObjectRef) -> tuple[ColumnRef, ...]:
        return ()

    def list_all_objects(self, conn: ConnectionHandle) -> Iterable[ObjectRef]:
        return list(self._objects)

    def close(self, conn: ConnectionHandle) -> None:
        self.closed += 1


class StaticTokenProvider:
    def get_token(self, scope: str = "https://database.windows.net/.default") -> AccessToken:
        return AccessToken(token="dummy", expires_on=datetime(2999, 1, 1, tzinfo=UTC))


class FrozenClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def _make_uc(
    dacpac_objs: list[ObjectRef], db_objs: list[ObjectRef]
) -> tuple[GenerateDropScriptUseCase, FakeSqlMetadataService]:
    sql_meta = FakeSqlMetadataService(db_objs)
    uc = GenerateDropScriptUseCase(
        dacpac=FakeDacPacService(dacpac_objs),  # type: ignore[arg-type]
        sql_meta=sql_meta,  # type: ignore[arg-type]
        token_provider=StaticTokenProvider(),
        clock=FrozenClock(),
    )
    return uc, sql_meta


def _request(tmp_path: Path, schemas: frozenset[str] | None = None) -> DropScriptRequest:
    return DropScriptRequest(
        dacpac_path=tmp_path / "fake.dacpac",
        connection=LiveConnection(server="srv.example.net", database="warehouse"),
        output_dir=tmp_path / "out",
        schemas_filter=schemas,
    )


def test_orphans_are_dropped(tmp_path: Path) -> None:
    dacpac = [_ref("dbo", "KeepTable", ObjectType.TABLE)]
    db = [
        _ref("dbo", "KeepTable", ObjectType.TABLE),
        _ref("dbo", "OrphanTable", ObjectType.TABLE),
        _ref("dbo", "OrphanProc", ObjectType.PROCEDURE),
    ]
    uc, sql_meta = _make_uc(dacpac, db)
    result = uc.execute(_request(tmp_path))

    text = result.script_path.read_text()
    assert "DROP TABLE IF EXISTS [dbo].[OrphanTable];" in text
    assert "DROP PROCEDURE IF EXISTS [dbo].[OrphanProc];" in text
    assert "KeepTable" not in text
    assert {r.name for r in result.dropped} == {"OrphanTable", "OrphanProc"}
    assert sql_meta.opened == 1 and sql_meta.closed == 1


def test_schema_filter_excludes_other_schemas(tmp_path: Path) -> None:
    dacpac: list[ObjectRef] = []
    db = [
        _ref("dbo", "A", ObjectType.TABLE),
        _ref("audit", "B", ObjectType.TABLE),
    ]
    uc, _ = _make_uc(dacpac, db)
    result = uc.execute(_request(tmp_path, schemas=frozenset({"dbo"})))

    text = result.script_path.read_text()
    assert "[dbo].[A]" in text
    assert "[audit].[B]" not in text
    assert [r.schema_name for r in result.dropped] == ["dbo"]


def test_case_insensitive_match(tmp_path: Path) -> None:
    dacpac = [_ref("DBO", "T", ObjectType.TABLE)]
    db = [_ref("dbo", "t", ObjectType.TABLE)]
    uc, _ = _make_uc(dacpac, db)
    result = uc.execute(_request(tmp_path))
    assert result.dropped == ()


def test_drop_order(tmp_path: Path) -> None:
    dacpac: list[ObjectRef] = []
    db = [
        _ref("dbo", "T", ObjectType.TABLE),
        _ref("dbo", "V", ObjectType.VIEW),
        _ref("dbo", "P", ObjectType.PROCEDURE),
        _ref("dbo", "SYN", ObjectType.SYNONYM),
    ]
    uc, _ = _make_uc(dacpac, db)
    result = uc.execute(_request(tmp_path))
    text = result.script_path.read_text()

    order = {name: text.index(name) for name in ("[dbo].[SYN]", "[dbo].[V]", "[dbo].[P]", "[dbo].[T]")}
    assert order["[dbo].[SYN]"] < order["[dbo].[V]"] < order["[dbo].[P]"] < order["[dbo].[T]"]


def test_if_exists_guard(tmp_path: Path) -> None:
    dacpac: list[ObjectRef] = []
    db = [
        _ref("dbo", "T", ObjectType.TABLE),
        _ref("dbo", "V", ObjectType.VIEW),
        _ref("dbo", "F", ObjectType.SCALAR_FUNCTION),
        _ref("ext", "X", ObjectType.EXTERNAL_TABLE),
    ]
    uc, _ = _make_uc(dacpac, db)
    result = uc.execute(_request(tmp_path))
    for line in result.script_path.read_text().splitlines():
        if line.startswith("DROP "):
            assert "IF EXISTS" in line


def test_no_orphans_emits_empty_but_valid_script(tmp_path: Path) -> None:
    ref = _ref("dbo", "OnlyOne", ObjectType.TABLE)
    uc, _ = _make_uc([ref], [ref])
    result = uc.execute(_request(tmp_path))
    text = result.script_path.read_text()
    assert result.dropped == ()
    assert "0 orphan object(s)" in text
    assert "DROP " not in text
    assert result.script_path.exists()


def test_unknown_types_produce_warnings(tmp_path: Path) -> None:
    dacpac: list[ObjectRef] = []
    db = [_ref("dbo", "Mystery", ObjectType.UNKNOWN)]
    uc, _ = _make_uc(dacpac, db)
    result = uc.execute(_request(tmp_path))
    assert result.dropped == ()
    assert any("Mystery" in w for w in result.warnings)


def test_output_filename_includes_server_and_db(tmp_path: Path) -> None:
    uc, _ = _make_uc([], [])
    result = uc.execute(_request(tmp_path))
    assert result.script_path.name.startswith("drop_script_srv_example_net__warehouse_")
    assert result.script_path.suffix == ".sql"
