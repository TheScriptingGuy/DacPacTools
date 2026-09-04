from __future__ import annotations

from dacpactools.application.ports.clock import IClock, SystemClock
from dacpactools.application.ports.dacpac_service import IDacPacService
from dacpactools.application.ports.lineage_writer import ILineageWriter
from dacpactools.application.ports.scriptdom_parser import IScriptDomParser
from dacpactools.application.ports.sql_metadata_service import ISqlMetadataService
from dacpactools.application.ports.token_provider import ITokenProvider
from dacpactools.application.ports.viz_renderer import IVizRenderer


def test_ports_are_protocols() -> None:
    for proto in (
        IDacPacService,
        ISqlMetadataService,
        IScriptDomParser,
        ILineageWriter,
        IVizRenderer,
        ITokenProvider,
        IClock,
    ):
        assert hasattr(proto, "_is_protocol")


def test_system_clock_satisfies_iclock() -> None:
    c: IClock = SystemClock()
    assert c.now_utc().tzinfo is not None
