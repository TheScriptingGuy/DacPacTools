# DacPacTools

Python CLI that builds object-to-base-table **column-level lineage** for SQL Server /
Azure SQL. Combines the DacFx `TSqlModel` object graph with T-SQL AST analysis
(ScriptDom) to trace every column of a target object back to its base tables,
emitting **OpenLineage-compliant JSON** plus an **interactive HTML** visualization
(cytoscape.js + dagre) that you can open in a browser and click through.

## Architecture

Onion / Clean architecture — outer layers depend inward only.

```
src/dacpactools/
├── domain/              # frozen pydantic models, zero I/O
├── application/         # use cases + Protocol ports
├── infrastructure/      # DacFx / ScriptDom / Azure / writers / viz adapters
└── presentation/        # click CLI + composition root
```

## Runtime prerequisites

- **Python 3.12** (uv will fetch it)
- **.NET 8 Desktop Runtime** OR **.NET Framework 4.7.2+**
- **sqlpackage** as a dotnet global tool — bundles all DacFx transitive deps.
  Without this, DacFx types silently fail to load under pythonnet.

```pwsh
dotnet tool install --global microsoft.sqlpackage
```

DacPacTools speaks to .NET via `pythonnet`. Runtime is auto-detected but can be
forced with `--runtime coreclr|netfx` or `DOTNET_RUNTIME=`.

## Quickstart

```bash
uv sync
uv run python -m dacpactools tools install-dacfx     # populates .tools/nuget cache
uv run python -m dacpactools doctor --probe-clr      # verifies CLR + assemblies load
```

Build lineage from a .dacpac:

```bash
uv run python -m dacpactools lineage `
    --object "[TD_JRL].[Create_DMS_TD_HLP]" `
    --dacpac ./Datafundament.dacpac `
    --out ./out
```

Build lineage from a live Azure SQL DB (uses `DefaultAzureCredential`):

```bash
# .env
DB__PROD__SERVER=myserver.database.windows.net
DB__PROD__DATABASE=warehouse
```

```bash
uv run python -m dacpactools lineage \
    --object "[dbo].[usp_LoadFact]" \
    --connection prod \
    --out ./out
```

Outputs:
- `out/lineage_<object>_<timestamp>.json` — OpenLineage `RunEvent` with a custom
  `dacpactools_tree` facet holding the full nested lineage graph
- `out/lineage_<object>.html` — self-contained interactive graph (opens in browser)

## Belastingdienst / WDAC note

Pip-generated `.exe` shims under `.venv\Scripts\` are blocked by corporate
Attack Surface Reduction (`Toegang geweigerd. (os error 5)`). Always invoke via
`python -m`:

```bash
uv run python -m dacpactools ...
uv run python -m pytest ...
```

## Testing

```pwsh
uv run python -m pytest tests/unit -q                 # fast unit tests, no CLR
uv run python -m pytest tests/integration -q -m dotnet  # DacFx / ScriptDom via pythonnet
RUN_AZURE_TESTS=1 uv run python -m pytest tests -m azure  # live Azure SQL
```

The integration suite compiles fixture .sql to a real .dacpac in-process
(no external sqlpackage invocation needed).

Build a stand-alone .dacpac from .sql:

```bash
uv run python tests/tools/build_fixture.py \
    --src tests/fixtures/sql \
    --out tests/fixtures/dacpacs/sample.dacpac \
    --name sample
```

## Supported T-SQL constructs (column-level lineage)

| Construct                              | Status |
|----------------------------------------|--------|
| `SELECT` (QuerySpecification)          | yes    |
| Aliases + `QualifiedJoin`              | yes    |
| `SELECT *` / `t.*` expansion           | yes    |
| CTE (`WITH ... AS (...)`)              | yes    |
| Derived table (subquery in `FROM`)     | yes    |
| `UNION` / `EXCEPT` / `INTERSECT` (ordinal merge) | yes |
| `INSERT ... SELECT` (ordinal to target)| yes    |
| `INSERT ... VALUES`                    | yes    |
| `MERGE` (UPDATE + INSERT actions)      | yes    |
| Function calls, CASE, computed expr    | marked `is_direct=false` with all source cols |
| Dynamic SQL (`EXEC(@sql)`)             | warns; skipped |
| Cross-database references              | warns; single-model boundary |
| Temp tables (`#t`), table variables    | not resolved (future) |

## Orchestrator lineage (ADF / Synapse / Fabric / Airflow)

The `orchestrator` subcommand parses pipeline/DAG artifacts on disk, extracts
SQL activities that run against a SQL environment, feeds each SQL statement
through the existing analyzer, and emits a combined
`pipeline → activity → sql → dataset → columns` graph.

```bash
uv run python -m dacpactools orchestrator \
    --artifact ./adf/pipelines/pl_LoadFact.json \
    --env-map ./env_map.json \
    --out ./out
```

Point `--artifact` at a single file or a directory (recursively scanned). Use
`--type adf|airflow|auto` to force artifact type; the default (`auto`) detects
per file:

- `.json` file whose top-level `properties.activities` is a list, or an ARM
  template containing a `Microsoft.DataFactory/factories/pipelines` resource →
  **ADF** (also covers Synapse Data Factory and Fabric Data Factory).
- `.py` file that imports `airflow` → **Airflow**.

### Env-map file

Maps ADF linked-service names or Airflow conn_ids to either an existing
`DB__<name>` connection or a dacpac file:

```json
{
  "version": 1,
  "mappings": {
    "AzureSqlDatabase1": {"kind": "dacpac",     "path": "./artifacts/Sales.dacpac"},
    "mssql_default":     {"kind": "connection", "name": "sales"}
  }
}
```

Relative `path` values resolve against the env-map file's directory. Connection
names look up `DB__<name>__server / DB__<name>__database` via `Settings`.

### Supported activities (v1)

| Orchestrator | Activity / operator                                   | SQL extraction                                    |
|--------------|-------------------------------------------------------|---------------------------------------------------|
| ADF          | `SqlServerStoredProcedure`                            | synthesised `EXEC [schema].[proc] @p1=...`        |
| ADF          | `Script`                                              | concat `typeProperties.scripts[].text`            |
| ADF          | `Lookup` (`SqlSource`/`AzureSqlSource`/`SqlDWSource`) | `source.sqlReaderQuery` or synthesised EXEC       |
| ADF          | `Copy` (SQL source with query)                        | `source.sqlReaderQuery` (table-only → skipped)    |
| Airflow      | `MsSqlOperator`, `SQLExecuteQueryOperator`            | `sql=` literal, list, or `.sql` template file     |

Control-flow activities (`IfCondition`, `ForEach`, `Until`, `Switch`) are
recursed into for nested SQL activities. Fabric / Synapse activity type aliases
are handled via a lookup table plus a permissive "has `sqlReaderQuery` or
`storedProcedureName`" fallback that emits `UNSUPPORTED` with a warning.

Every SQL activity whose text starts with a bare `EXEC [schema].[proc]` is
delegated to the existing lineage use case (full column-level lineage against
the target dacpac). Other SQL emits a placeholder graph plus a
`statement-level analysis only for non-EXEC SQL` warning.

### Airflow static-parse limits

The DAG reader uses Python `ast.parse` and never executes DAG files. It cannot
follow:

- SQL assembled from function returns or Jinja templates at runtime.
- Operator subclasses defined outside the DAG file.
- Operators generated inside a `TaskGroup` loop or `if __name__` block.
- `sql=` values that are f-strings or other non-constant expressions (a
  `dynamic sql= expression` warning is emitted).

### Output

- `out/orchestrator_<run>_<timestamp>.json` — one OpenLineage `RunEvent` with
  two custom facets:
  - `dacpactools_orchestrator_tree` — pipelines[], activities[], dependencies[];
  - `dacpactools_activity_lineage.graphs` — per-activity dataset subgraphs
    (same shape as the existing `dacpactools_tree` facet), keyed by
    `lineage_graph_id` on each activity so consumers can drill in.
- `out/orchestrator_<run>.html` — interactive Cytoscape graph with compound
  nodes (pipeline > activity > dataset). Click any node to inspect activity SQL,
  dependencies, or dataset columns and mappings.

## OpenLineage output shape

Hybrid:
- `outputs[0]` is the root object as a `Dataset`, with an OL `columnLineage`
  facet mapping each root column to its direct upstream columns.
- `inputs[]` is every non-root reachable object.
- `run.facets.dacpactools_tree` is a custom facet holding the full nested tree
  (all nodes, all edges, all per-node column mappings, all warnings) so
  consumers that need the intermediate hops don't lose data.

Namespace: `mssql://<server>/<database>` (defaults to the first `DB__*`
connection, or `mssql://local`).

## Development

```bash
uv sync
uv run python -m ruff check src tests
uv run python -m mypy src/dacpactools
uv run python -m pytest -q
```
