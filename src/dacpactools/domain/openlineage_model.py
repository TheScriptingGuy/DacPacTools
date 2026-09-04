from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_PRODUCER = "https://github.com/wesselvdlinden/dacpactools"
_SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"


class Facet(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    producer: str = Field(default=_PRODUCER, alias="_producer")
    schema_url: str = Field(default=_SCHEMA_URL, alias="_schemaURL")


class InputField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    name: str
    field: str
    transformations: list[dict[str, Any]] = Field(default_factory=list)


class ColumnLineageField(BaseModel):
    inputFields: list[InputField] = Field(default_factory=list)
    transformationDescription: str | None = None
    transformationType: str | None = None


class ColumnLineageFacet(Facet):
    fields: dict[str, ColumnLineageField] = Field(default_factory=dict)


class SchemaField(BaseModel):
    name: str
    type: str | None = None


class SchemaFacet(Facet):
    fields: list[SchemaField] = Field(default_factory=list)


class Dataset(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    namespace: str
    name: str
    facets: dict[str, Facet] = Field(default_factory=dict)


class Job(BaseModel):
    namespace: str
    name: str


class Run(BaseModel):
    runId: str


class RunEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    eventType: Literal["START", "COMPLETE", "ABORT", "FAIL", "OTHER"]
    eventTime: datetime
    run: Run
    job: Job
    inputs: list[Dataset] = Field(default_factory=list)
    outputs: list[Dataset] = Field(default_factory=list)
    producer: str = _PRODUCER
    schemaURL: str = _SCHEMA_URL
