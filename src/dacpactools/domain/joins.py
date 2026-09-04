from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from dacpactools.domain.identifiers import ObjectRef


class JoinPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_column: str
    other_column: str


class JoinUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    consumer: ObjectRef
    target: ObjectRef
    other: ObjectRef
    join_type: str
    target_alias: str | None = None
    other_alias: str | None = None
    pairs: tuple[JoinPair, ...] = ()
    on_expression: str | None = None


class JoinReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: ObjectRef
    usages: tuple[JoinUsage, ...] = ()
    warnings: tuple[str, ...] = ()
