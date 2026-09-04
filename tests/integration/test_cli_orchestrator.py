from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from dacpactools.presentation.cli import main

pytestmark = pytest.mark.dotnet


@pytest.fixture
def sample_dacpac(tmp_path: Path, tsql_model: Any) -> Path:
    from Microsoft.SqlServer.Dac import DacPackageExtensions, PackageMetadata  # type: ignore

    tsql_model.AddObjects("CREATE TABLE dbo.Fact (id int, amt decimal(10,2))")
    tsql_model.AddObjects("CREATE VIEW dbo.v_Fact AS SELECT id, amt FROM dbo.Fact")
    tsql_model.AddObjects("CREATE PROCEDURE dbo.usp_Load AS SELECT id FROM dbo.v_Fact")
    out = tmp_path / "sample.dacpac"
    metadata = PackageMetadata()
    metadata.Name = "sample"
    metadata.Version = "1.0.0"
    DacPackageExtensions.BuildPackage(str(out), tsql_model, metadata)
    return out


@pytest.fixture
def pipeline_json(tmp_path: Path) -> Path:
    payload = {
        "name": "pl_load",
        "properties": {
            "activities": [
                {
                    "name": "load_fact",
                    "type": "SqlServerStoredProcedure",
                    "linkedServiceName": {
                        "referenceName": "SalesDb",
                        "type": "LinkedServiceReference",
                    },
                    "typeProperties": {"storedProcedureName": "[dbo].[usp_Load]"},
                }
            ]
        },
    }
    p = tmp_path / "pl_load.json"
    p.write_text(json.dumps(payload))
    return p


@pytest.fixture
def env_map(tmp_path: Path, sample_dacpac: Path) -> Path:
    payload = {
        "version": 1,
        "mappings": {"SalesDb": {"kind": "dacpac", "path": str(sample_dacpac)}},
    }
    p = tmp_path / "env_map.json"
    p.write_text(json.dumps(payload))
    return p


def test_cli_orchestrator_end_to_end(
    pipeline_json: Path, env_map: Path, tmp_path: Path
) -> None:
    runner = CliRunner()
    out_dir = tmp_path / "out"
    result = runner.invoke(
        main,
        [
            "orchestrator",
            "--artifact",
            str(pipeline_json),
            "--env-map",
            str(env_map),
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "orchestrator lineage built" in result.output

    json_files = list(out_dir.glob("orchestrator_*.json"))
    html_files = list(out_dir.glob("orchestrator_*.html"))
    assert len(json_files) == 1
    assert len(html_files) == 1

    payload = json.loads(json_files[0].read_text())
    facets = payload["run"]["facets"]
    orch = facets["dacpactools_orchestrator_tree"]
    assert orch["runName"]
    assert len(orch["pipelines"]) == 1
    pipeline = orch["pipelines"][0]
    assert pipeline["name"] == "pl_load"
    assert len(pipeline["activities"]) == 1
    activity = pipeline["activities"][0]
    assert activity["activityId"] == "load_fact"
    assert activity["envRef"] == "SalesDb"
    assert activity["lineageGraphId"]

    activity_graphs = facets["dacpactools_activity_lineage"]["graphs"]
    assert activity["lineageGraphId"] in activity_graphs
    subgraph = activity_graphs[activity["lineageGraphId"]]
    node_refs = {n["ref"] for n in subgraph["nodes"]}
    assert "dbo.usp_Load" in node_refs
    assert "dbo.v_Fact" in node_refs
    assert "dbo.Fact" in node_refs
