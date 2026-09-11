from __future__ import annotations

from dataclasses import dataclass, field

from dacpactools.domain.identifiers import ColumnRef, ObjectRef


@dataclass
class TableBinding:
    """A single FROM-clause table binding: alias => (source ObjectRef, columns)."""
    alias: str
    source_ref: ObjectRef | None
    columns: tuple[ColumnRef, ...]


@dataclass
class QueryScope:
    """Scope of a single QuerySpecification: alias -> binding."""
    bindings: dict[str, TableBinding] = field(default_factory=dict)
    parent: QueryScope | None = None

    def add(self, binding: TableBinding) -> None:
        self.bindings[binding.alias.lower()] = binding

    def lookup_by_alias(self, alias: str) -> TableBinding | None:
        cur: QueryScope | None = self
        alias_l = alias.lower()
        while cur is not None:
            if alias_l in cur.bindings:
                return cur.bindings[alias_l]
            cur = cur.parent
        return None

    def resolve_column(self, table_prefix: str | None, col_name: str) -> tuple[ColumnRef, ...]:
        """Resolve a MultiPartIdentifier column reference to source ColumnRef(s)."""
        col_l = col_name.lower()
        if table_prefix:
            binding = self.lookup_by_alias(table_prefix)
            if binding is None:
                return ()
            return tuple(c for c in binding.columns if c.column_name.lower() == col_l)

        matches: list[ColumnRef] = []
        cur: QueryScope | None = self
        while cur is not None:
            for binding in cur.bindings.values():
                matches.extend(c for c in binding.columns if c.column_name.lower() == col_l)
            if matches:
                return tuple(matches)
            cur = cur.parent
        return ()

    def all_columns_expanded(self, table_prefix: str | None = None) -> tuple[ColumnRef, ...]:
        """Expand SELECT * or `t.*` to full column list."""
        if table_prefix:
            binding = self.lookup_by_alias(table_prefix)
            return binding.columns if binding else ()
        out: list[ColumnRef] = []
        for binding in self.bindings.values():
            out.extend(binding.columns)
        return tuple(out)
