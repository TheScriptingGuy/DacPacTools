from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import click

from dacpactools.application.dto.orchestrator_request import (
    ArtifactType,
    BuildOrchestratorLineageRequest,
)
from dacpactools.domain.errors import (
    DomainError,
    UnresolvableOrchestratorArtifact,
)
from dacpactools.presentation.composition import (
    Container,
    build_orchestrator_lineage_use_case,
)
from dacpactools.presentation.logging_config import get_logger

log = get_logger("cli.orchestrator")


@click.command("orchestrator")
@click.option(
    "--artifact",
    "artifact_path",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="File or directory containing ADF pipeline JSON and/or Airflow DAG .py files.",
)
@click.option(
    "--env-map",
    "env_map_path",
    type=click.Path(dir_okay=False, path_type=Path, exists=True),
    required=True,
    help="JSON env-map: linked-service / conn_id -> dacpac path or DB connection name.",
)
@click.option(
    "--out",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("./out"),
)
@click.option(
    "--format",
    "formats",
    multiple=True,
    type=click.Choice(["json", "html"]),
    default=("json", "html"),
    help="Emit formats.",
)
@click.option(
    "--type",
    "artifact_type",
    type=click.Choice(["adf", "airflow", "auto"]),
    default="auto",
    help="Force artifact type. 'auto' detects per file by content/extension.",
)
@click.option("--max-depth", type=int, default=None)
@click.pass_obj
def orchestrator(
    container: Container,
    artifact_path: Path,
    env_map_path: Path,
    output_dir: Path,
    formats: tuple[str, ...],
    artifact_type: str,
    max_depth: int | None,
) -> None:
    """Build pipeline/DAG column lineage across orchestrator SQL activities."""
    req = BuildOrchestratorLineageRequest(
        artifact_path=artifact_path,
        env_map_path=env_map_path,
        output_dir=output_dir,
        artifact_type=cast(ArtifactType, artifact_type),
        emit_formats=frozenset(formats),
        max_depth=max_depth,
    )

    try:
        use_case = build_orchestrator_lineage_use_case(container.settings)
    except Exception as e:  # noqa: BLE001
        click.echo(f"error: CLR / assembly initialisation failed: {e}", err=True)
        sys.exit(4)

    try:
        result = use_case.execute(req)
    except UnresolvableOrchestratorArtifact as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    except DomainError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(3)
    except Exception as e:
        log.exception("orchestrator lineage failed", err=str(e))
        click.echo(f"error: {type(e).__name__}: {e}", err=True)
        sys.exit(1)

    graph = result.graph
    total_activities = sum(len(p.activities) for p in graph.run.pipelines)
    click.echo(
        f"orchestrator lineage built: {len(graph.run.pipelines)} pipelines, "
        f"{total_activities} activities, {len(graph.lineage_graphs)} lineage graphs"
    )
    for kind, path in result.artifacts.items():
        click.echo(f"  {kind}: {path}")

    for w in result.warnings:
        click.echo(f"  warning: {w}", err=True)
    for p in graph.run.pipelines:
        for a in p.activities:
            for w in a.warnings:
                click.echo(f"  warning: [{p.name}/{a.activity_id}] {w}", err=True)
