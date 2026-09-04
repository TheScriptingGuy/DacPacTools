from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from dacpactools.domain.enums import ObjectType
from dacpactools.domain.errors import InvalidObjectName

_PART = r"(?:\[(?P<{name}_b>[^\]]+)\]|(?P<{name}_u>[A-Za-z_][A-Za-z0-9_]*))"
_ONE = re.compile(r"^" + _PART.format(name="a") + r"$")
_TWO = re.compile(r"^" + _PART.format(name="a") + r"\." + _PART.format(name="b") + r"$")
_THREE = re.compile(
    r"^" + _PART.format(name="a") + r"\." + _PART.format(name="b") + r"\." + _PART.format(name="c") + r"$"
)


def _pick(m: re.Match[str], name: str) -> str:
    return m.group(f"{name}_b") or m.group(f"{name}_u")


class ObjectRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    database: str | None = None
    schema_name: str = Field(alias="schema")
    name: str
    object_type: ObjectType = ObjectType.UNKNOWN

    @classmethod
    def parse(
        cls,
        raw: str,
        *,
        default_schema: str = "dbo",
        object_type: ObjectType = ObjectType.UNKNOWN,
    ) -> Self:
        text = raw.strip()
        if not text:
            raise InvalidObjectName("empty object name")
        for pattern, arity in ((_THREE, 3), (_TWO, 2), (_ONE, 1)):
            m = pattern.match(text)
            if m:
                if arity == 3:
                    return cls(
                        database=_pick(m, "a"),
                        schema=_pick(m, "b"),
                        name=_pick(m, "c"),
                        object_type=object_type,
                    )
                if arity == 2:
                    return cls(
                        database=None,
                        schema=_pick(m, "a"),
                        name=_pick(m, "b"),
                        object_type=object_type,
                    )
                return cls(
                    database=None,
                    schema=default_schema,
                    name=_pick(m, "a"),
                    object_type=object_type,
                )
        raise InvalidObjectName(f"could not parse object name: {raw!r}")

    def qualified(self) -> str:
        parts = [f"[{self.schema_name}]", f"[{self.name}]"]
        if self.database:
            parts.insert(0, f"[{self.database}]")
        return ".".join(parts)

    def key(self) -> tuple[str | None, str, str]:
        return (self.database, self.schema_name.lower(), self.name.lower())


class ColumnRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: ObjectRef
    column_name: str
    ordinal: int | None = None
    data_type: str | None = None

    def qualified(self) -> str:
        return f"{self.owner.qualified()}.[{self.column_name}]"
