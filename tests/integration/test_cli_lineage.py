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
    """Build a real .dacpac fixture from an in-memory TSqlModel."""
    from Microsoft.SqlServer.Dac import DacPackageExtensions, PackageMetadata  # type: ignore  # noqa: PLC0415

    tsql_model.AddObjects("CREATE TABLE dbo.Fact (id int, amt decimal(10,2))")
    tsql_model.AddObjects("CREATE VIEW dbo.v_Fact AS SELECT id, amt FROM dbo.Fact")
    tsql_model.AddObjects("CREATE PROCEDURE dbo.usp_Load AS SELECT id FROM dbo.v_Fact")
    out = tmp_path / "sample.dacpac"
    metadata = PackageMetadata()
    metadata.Name = "sample"
    metadata.Version = "1.0.0"
    DacPackageExtensions.BuildPackage(str(out), tsql_model, metadata)
    return out


def test_cli_lineage_dacpac_end_to_end(sample_dacpac: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    out_dir = tmp_path / "out"
    result = runner.invoke(
        main,
        [
            "lineage",
            "--object", "[dbo].[usp_Load]",
            "--dacpac", str(sample_dacpac),
            "--out", str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "lineage built" in result.output
    json_files = list(out_dir.glob("lineage_*.json"))
    html_files = list(out_dir.glob("lineage_*.html"))
    assert len(json_files) == 1
    assert len(html_files) == 1

    payload = json.loads(json_files[0].read_text())
    assert payload["outputs"][0]["name"] == "dbo.usp_Load"
    tree = payload["run"]["facets"]["dacpactools_tree"]
    node_names = {n["ref"] for n in tree["nodes"]}
    assert "dbo.usp_Load" in node_names
    assert "dbo.v_Fact" in node_names
    assert "dbo.Fact" in node_names
