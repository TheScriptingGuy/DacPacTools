from __future__ import annotations

from pathlib import Path

from dacpactools.domain.enums import ActivityType, OrchestratorKind
from dacpactools.infrastructure.orchestrator.adf_artifact_reader import (
    AdfArtifactReader,
)

_FIX = Path(__file__).resolve().parents[3] / "fixtures" / "orchestrator" / "adf"


def test_can_read_recognises_adf_pipeline_json() -> None:
    assert AdfArtifactReader().can_read(_FIX / "pipeline_storedproc.json") is True


def test_can_read_rejects_non_adf_json(tmp_path: Path) -> None:
    p = tmp_path / "not_adf.json"
    p.write_text('{"hello": "world"}')
    assert AdfArtifactReader().can_read(p) is False


def test_can_read_rejects_non_json_suffix(tmp_path: Path) -> None:
    p = tmp_path / "readme.txt"
    p.write_text('{"properties": {"activities": []}}')
    assert AdfArtifactReader().can_read(p) is False


def test_read_storedproc_synthesises_exec_and_captures_deps() -> None:
    pipelines = AdfArtifactReader().read(_FIX / "pipeline_storedproc.json")
    assert len(pipelines) == 1
    p = pipelines[0]
    assert p.kind == OrchestratorKind.ADF
    assert p.name == "pl_loadFact"
    assert [a.name for a in p.activities] == ["trunc_stage", "load_fact"]

    trunc = p.activities[0]
    assert trunc.activity_type == ActivityType.SQL_STORED_PROCEDURE
    assert trunc.sql_text == "EXEC [dbo].[usp_TruncStage]"
    assert trunc.env_ref == "AzureSqlDatabase1"

    load = p.activities[1]
    assert load.sql_text is not None
    assert load.sql_text.startswith("EXEC [dbo].[usp_LoadFact] ")
    assert "@batchDate = '2025-01-01'" in load.sql_text
    assert "@isFull = 1" in load.sql_text
    assert load.depends_on == ("trunc_stage",)

    assert len(p.activity_edges) == 1
    assert (
        p.activity_edges[0].from_activity_id,
        p.activity_edges[0].to_activity_id,
    ) == ("trunc_stage", "load_fact")


def test_read_lookup_copy_marks_table_only_as_unsupported() -> None:
    pipelines = AdfArtifactReader().read(_FIX / "pipeline_lookup_copy.json")
    by_name = {a.name: a for a in pipelines[0].activities}

    assert by_name["check_source"].activity_type == ActivityType.SQL_LOOKUP
    assert "SELECT COUNT(*)" in (by_name["check_source"].sql_text or "")

    assert by_name["copy_query"].activity_type == ActivityType.SQL_COPY_SOURCE
    assert "SELECT a, b" in (by_name["copy_query"].sql_text or "")

    copy_table = by_name["copy_table"]
    assert copy_table.activity_type == ActivityType.UNSUPPORTED
    assert copy_table.sql_text is None
    assert any("table-only" in w for w in copy_table.warnings)


def test_read_foreach_recurses_into_nested_activities() -> None:
    pipelines = AdfArtifactReader().read(_FIX / "pipeline_foreach_nested.json")
    activities = pipelines[0].activities
    names = [a.name for a in activities]
    assert "for_each_month" in names
    assert "run_script" in names
    script = next(a for a in activities if a.name == "run_script")
    assert script.activity_type == ActivityType.SQL_SCRIPT
    assert script.sql_text is not None
    assert "TRUNCATE TABLE dbo.Stage" in script.sql_text
    assert "INSERT INTO dbo.Stage" in script.sql_text
