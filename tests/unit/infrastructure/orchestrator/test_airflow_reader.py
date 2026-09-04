from __future__ import annotations

from pathlib import Path

from dacpactools.domain.enums import ActivityType, OrchestratorKind
from dacpactools.infrastructure.orchestrator.airflow_dag_reader import (
    AirflowDagReader,
)

_FIX = Path(__file__).resolve().parents[3] / "fixtures" / "orchestrator" / "airflow"


def test_can_read_accepts_airflow_dag() -> None:
    assert AirflowDagReader().can_read(_FIX / "dag_simple.py") is True


def test_can_read_rejects_non_airflow_py(tmp_path: Path) -> None:
    p = tmp_path / "plain.py"
    p.write_text("print('hi')")
    assert AirflowDagReader().can_read(p) is False


def test_read_dag_simple_extracts_two_tasks_and_edge() -> None:
    pipelines = AirflowDagReader().read(_FIX / "dag_simple.py")
    assert len(pipelines) == 1
    p = pipelines[0]
    assert p.kind == OrchestratorKind.AIRFLOW
    assert p.name == "dag_simple"

    ids = {a.activity_id for a in p.activities}
    assert ids == {"load_staging", "load_fact"}

    load_stage = next(a for a in p.activities if a.activity_id == "load_staging")
    assert load_stage.env_ref == "mssql_default"
    assert load_stage.sql_text == "INSERT INTO dbo.Stage SELECT * FROM dbo.Source"
    assert load_stage.activity_type == ActivityType.SQL_QUERY

    assert len(p.activity_edges) == 1
    edge = p.activity_edges[0]
    assert (edge.from_activity_id, edge.to_activity_id) == ("load_staging", "load_fact")

    load_fact = next(a for a in p.activities if a.activity_id == "load_fact")
    assert load_fact.depends_on == ("load_staging",)


def test_read_dag_template_loads_sql_from_file() -> None:
    pipelines = AirflowDagReader().read(_FIX / "dag_template.py")
    p = pipelines[0]
    assert len(p.activities) == 1
    a = p.activities[0]
    assert a.activity_id == "load_dim"
    assert a.env_ref == "mssql_warehouse"
    assert a.sql_text is not None
    assert "INSERT INTO dbo.DimCustomer" in a.sql_text
    assert a.source_uri is not None
    assert a.source_uri.endswith("load_dim.sql")


def test_read_dag_dynamic_warns_and_skips_sql() -> None:
    pipelines = AirflowDagReader().read(_FIX / "dag_dynamic.py")
    a = pipelines[0].activities[0]
    assert a.activity_id == "dynamic_sql"
    assert a.sql_text is None
    assert a.activity_type == ActivityType.UNSUPPORTED
    assert any("dynamic sql= expression" in w for w in a.warnings)
