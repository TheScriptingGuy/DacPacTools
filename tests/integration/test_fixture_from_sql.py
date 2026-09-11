from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from dacpactools.presentation.cli import main

pytestmark = pytest.mark.dotnet


def test_build_fixture_and_run_lineage(tmp_path: Path, clr_initialized: None) -> None:
    from Microsoft.SqlServer.Dac import DacPackageExtensions, PackageMetadata  # type: ignore
    from Microsoft.SqlServer.Dac.Model import (  # type: ignore
        SqlServerVersion,
        TSqlModel,
        TSqlModelOptions,
    )

    fixture_sql = Path(__file__).parent.parent / "fixtures" / "sql" / "schema.sql"
    dacpac = tmp_path / "fixture.dacpac"

    model = TSqlModel(SqlServerVersion.SqlAzure, TSqlModelOptions())
    try:
        text = fixture_sql.read_text()
        for stmt in [s.strip() for s in text.split("\n\n") if s.strip()]:
            model.AddObjects(stmt)
        metadata = PackageMetadata()
        metadata.Name = "fixture"
        metadata.Version = "1.0.0"
        DacPackageExtensions.BuildPackage(str(dacpac), model, metadata)
    finally:
        model.Dispose()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "lineage",
            "--object", "[dbo].[usp_TopSpenders]",
            "--dacpac", str(dacpac),
            "--out", str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 0, result.output

    # Should trace through the CTE + view to base tables.
    import json

    payload = json.loads(next((tmp_path / "out").glob("lineage_*.json")).read_text())
    tree = payload["run"]["facets"]["dacpactools_tree"]
    node_refs = {n["ref"] for n in tree["nodes"]}
    assert "dbo.usp_TopSpenders" in node_refs
    assert "dbo.v_CustomerOrders" in node_refs
    # Base tables reachable
    assert {"dbo.Customers", "dbo.Orders"}.issubset(node_refs)
