from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dacpactools.application.ports.clock import IClock, SystemClock
from dacpactools.presentation.logging_config import configure_logging, get_logger
from dacpactools.presentation.settings import Settings

if TYPE_CHECKING:
    from dacpactools.application.use_cases.build_lineage import BuildLineageUseCase
    from dacpactools.application.use_cases.build_orchestrator_lineage import (
        BuildOrchestratorLineageUseCase,
    )
    from dacpactools.application.use_cases.find_column_usage import (
        FindColumnUsageUseCase,
    )
    from dacpactools.application.use_cases.find_joins import FindJoinsUseCase
    from dacpactools.application.use_cases.generate_drop_script import (
        GenerateDropScriptUseCase,
    )
    from dacpactools.application.use_cases.recommend_index_stats import (
        RecommendIndexStatsUseCase,
    )


@dataclass(frozen=True)
class Container:
    settings: Settings


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    log = get_logger("composition")
    log.debug(
        "container built",
        runtime=settings.dotnet_runtime,
        connections=list(settings.db.keys()),
    )
    return Container(settings=settings)


def build_lineage_use_case(settings: Settings) -> "BuildLineageUseCase":
    """Composition root for the lineage use case. Initialises CLR + all adapters."""
    from dacpactools.application.use_cases.build_lineage import BuildLineageUseCase  # noqa: PLC0415
    from dacpactools.infrastructure.dacpac.dacpac_service import DacFxDacPacService  # noqa: PLC0415
    from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize  # noqa: PLC0415
    from dacpactools.infrastructure.scriptdom.parser_service import ScriptDomParserService  # noqa: PLC0415
    from dacpactools.infrastructure.viz.cytoscape_renderer import CytoscapeHtmlRenderer  # noqa: PLC0415
    from dacpactools.infrastructure.writers.openlineage_json_writer import (  # noqa: PLC0415
        OpenLineageJsonWriter,
    )

    initialize(settings)
    clock: IClock = SystemClock()
    return BuildLineageUseCase(
        dacpac=DacFxDacPacService(),
        parser=ScriptDomParserService(),
        ol_writer=OpenLineageJsonWriter(namespace=_default_namespace(settings), clock=clock),
        viz=CytoscapeHtmlRenderer(),
        clock=clock,
    )


def build_orchestrator_lineage_use_case(
    settings: Settings,
) -> "BuildOrchestratorLineageUseCase":
    """Composition root for the orchestrator-lineage use case. Reuses the inner
    lineage use case (which performs CLR bootstrap)."""
    from dacpactools.application.ports._handles import LiveConnection  # noqa: PLC0415
    from dacpactools.application.use_cases.build_orchestrator_lineage import (  # noqa: PLC0415
        BuildOrchestratorLineageUseCase,
    )
    from dacpactools.infrastructure.orchestrator.adf_artifact_reader import (  # noqa: PLC0415
        AdfArtifactReader,
    )
    from dacpactools.infrastructure.orchestrator.airflow_dag_reader import (  # noqa: PLC0415
        AirflowDagReader,
    )
    from dacpactools.infrastructure.orchestrator.cytoscape_orchestrator_renderer import (  # noqa: PLC0415
        CytoscapeOrchestratorRenderer,
    )
    from dacpactools.infrastructure.orchestrator.env_map_service import (  # noqa: PLC0415
        JsonEnvMapService,
    )
    from dacpactools.infrastructure.orchestrator.openlineage_orchestrator_writer import (  # noqa: PLC0415
        OpenLineageOrchestratorWriter,
    )

    inner = build_lineage_use_case(settings)

    def _resolve_conn(name: str) -> LiveConnection | None:
        try:
            db = settings.resolve_connection(name)
        except KeyError:
            return None
        return LiveConnection(server=db.server, database=db.database)

    return BuildOrchestratorLineageUseCase(
        readers=(AdfArtifactReader(), AirflowDagReader()),
        env_map=JsonEnvMapService(connection_resolver=_resolve_conn),
        inner=inner,
        parser=inner.parser,
        ol_writer=OpenLineageOrchestratorWriter(
            namespace=_default_namespace(settings), clock=inner.clock
        ),
        viz=CytoscapeOrchestratorRenderer(),
        clock=inner.clock,
    )


def build_find_joins_use_case(settings: Settings) -> "FindJoinsUseCase":
    from dacpactools.application.use_cases.find_joins import FindJoinsUseCase  # noqa: PLC0415
    from dacpactools.infrastructure.dacpac.dacpac_service import DacFxDacPacService  # noqa: PLC0415
    from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize  # noqa: PLC0415
    from dacpactools.infrastructure.scriptdom.parser_service import ScriptDomParserService  # noqa: PLC0415

    initialize(settings)
    return FindJoinsUseCase(dacpac=DacFxDacPacService(), parser=ScriptDomParserService())


def build_find_column_usage_use_case(settings: Settings) -> "FindColumnUsageUseCase":
    from dacpactools.application.use_cases.find_column_usage import (  # noqa: PLC0415
        FindColumnUsageUseCase,
    )
    from dacpactools.infrastructure.dacpac.dacpac_service import DacFxDacPacService  # noqa: PLC0415
    from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize  # noqa: PLC0415
    from dacpactools.infrastructure.scriptdom.parser_service import (  # noqa: PLC0415
        ScriptDomParserService,
    )

    initialize(settings)
    return FindColumnUsageUseCase(
        dacpac=DacFxDacPacService(), parser=ScriptDomParserService()
    )


def build_drop_script_use_case(settings: Settings) -> "GenerateDropScriptUseCase":
    from dacpactools.application.use_cases.generate_drop_script import (  # noqa: PLC0415
        GenerateDropScriptUseCase,
    )
    from dacpactools.infrastructure.azure.sql_client_service import (  # noqa: PLC0415
        SqlClientMetadataService,
    )
    from dacpactools.infrastructure.azure.token_provider import (  # noqa: PLC0415
        DefaultAzureCredentialTokenProvider,
    )
    from dacpactools.infrastructure.dacpac.dacpac_service import DacFxDacPacService  # noqa: PLC0415
    from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize  # noqa: PLC0415

    initialize(settings)
    return GenerateDropScriptUseCase(
        dacpac=DacFxDacPacService(),
        sql_meta=SqlClientMetadataService(),
        token_provider=DefaultAzureCredentialTokenProvider(),
        clock=SystemClock(),
    )


def build_recommend_index_stats_use_case(
    settings: Settings,
) -> "RecommendIndexStatsUseCase":
    from dacpactools.application.use_cases.recommend_index_stats import (  # noqa: PLC0415
        RecommendIndexStatsUseCase,
    )
    from dacpactools.infrastructure.azure.sql_client_service import (  # noqa: PLC0415
        SqlClientMetadataService,
    )
    from dacpactools.infrastructure.azure.token_provider import (  # noqa: PLC0415
        DefaultAzureCredentialTokenProvider,
    )
    from dacpactools.infrastructure.dacpac.dacpac_service import DacFxDacPacService  # noqa: PLC0415
    from dacpactools.infrastructure.dotnet.clr_bootstrap import initialize  # noqa: PLC0415
    from dacpactools.infrastructure.scriptdom.parser_service import (  # noqa: PLC0415
        ScriptDomParserService,
    )

    initialize(settings)
    return RecommendIndexStatsUseCase(
        dacpac=DacFxDacPacService(),
        sql_meta=SqlClientMetadataService(),
        parser=ScriptDomParserService(),
        token_provider=DefaultAzureCredentialTokenProvider(),
        clock=SystemClock(),
    )


def _default_namespace(settings: Settings) -> str:
    if settings.db:
        first = next(iter(settings.db.values()))
        return f"mssql://{first.server}/{first.database}"
    return "mssql://local"
