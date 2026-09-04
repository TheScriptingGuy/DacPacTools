from __future__ import annotations

from typing import Protocol, runtime_checkable

from dacpactools.application.ports._handles import ColumnResolver, ParsedBatch, ParsedLineage
from dacpactools.domain.lineage import ColumnMapping, LineageNode


@runtime_checkable
class IScriptDomParser(Protocol):
    def parse(self, tsql: str) -> ParsedBatch: ...

    def extract_column_mappings(
        self,
        parsed: ParsedBatch,
        node: LineageNode,
        resolver: ColumnResolver,
    ) -> tuple[ColumnMapping, ...]: ...

    def extract_lineage(
        self,
        parsed: ParsedBatch,
        node: LineageNode,
        resolver: ColumnResolver,
    ) -> ParsedLineage: ...
