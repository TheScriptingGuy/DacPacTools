from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from dacpactools.domain.enums import ActivityType, OrchestratorKind
from dacpactools.domain.orchestrator import ActivityEdge, Pipeline, SqlActivity

_MSSQL_OPERATORS = {"MsSqlOperator", "SQLExecuteQueryOperator"}
_MSSQL_CONN_HINTS = ("mssql", "azure_sql", "azuresql", "azure-sql", "sqlserver")


class AirflowDagReader:
    kind: ClassVar[OrchestratorKind] = OrchestratorKind.AIRFLOW

    def can_read(self, path: Path) -> bool:
        if path.suffix.lower() != ".py":
            return False
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            return False
        return _imports_airflow(tree)

    def read(self, path: Path) -> tuple[Pipeline, ...]:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        aliases = _collect_aliases(tree)

        tasks: dict[str, SqlActivity] = {}
        counter = [0]
        for call in _iter_target_calls(tree, aliases):
            task = _build_task(call, path, counter)
            if task is not None:
                tasks[task.activity_id] = task

        var_to_task = _map_var_to_task(tree, aliases)
        edges = _collect_edges(tree, var_to_task, set(tasks.keys()))

        # attach dependencies back into activities
        preds_by_task: dict[str, list[str]] = {}
        for e in edges:
            preds_by_task.setdefault(e.to_activity_id, []).append(e.from_activity_id)
        final_tasks = tuple(
            t.model_copy(update={"depends_on": tuple(preds_by_task.get(t.activity_id, ()))})
            for t in tasks.values()
        )

        pipeline_id = path.stem
        return (
            Pipeline(
                pipeline_id=pipeline_id,
                name=pipeline_id,
                kind=OrchestratorKind.AIRFLOW,
                source_path=path,
                activities=final_tasks,
                activity_edges=tuple(edges),
            ),
        )


def _imports_airflow(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("airflow"):
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("airflow"):
                    return True
    return False


def _collect_aliases(tree: ast.Module) -> dict[str, str]:
    """Local-name → imported symbol name (last segment for module imports)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                aliases[local] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[-1]
                aliases[local] = alias.name
    return aliases


def _iter_target_calls(tree: ast.Module, aliases: dict[str, str]) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_target_operator(node, aliases):
            yield node


def _is_target_operator(call: ast.Call, aliases: dict[str, str]) -> bool:
    name = _call_name(call)
    if name is None:
        return False
    resolved = aliases.get(name, name)
    return name in _MSSQL_OPERATORS or resolved.split(".")[-1] in _MSSQL_OPERATORS


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _get_kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _literal_str(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _build_task(
    call: ast.Call, source_path: Path, counter: list[int]
) -> SqlActivity | None:
    task_id = _literal_str(_get_kw(call, "task_id"))
    if task_id is None:
        return None
    op_name = _call_name(call) or "Operator"

    counter[0] += 1
    order = counter[0]
    warnings: list[str] = []

    conn_id = _literal_str(_get_kw(call, "conn_id")) or _literal_str(
        _get_kw(call, "mssql_conn_id")
    )
    if conn_id is None:
        warnings.append(f"task '{task_id}' has no conn_id/mssql_conn_id literal")

    sql_text: str | None = None
    source_uri: str | None = None
    sql_node = _get_kw(call, "sql")
    if sql_node is None:
        warnings.append(f"task '{task_id}' has no sql= parameter")
    elif isinstance(sql_node, ast.Constant) and isinstance(sql_node.value, str):
        val = sql_node.value
        if _looks_like_template_path(val):
            resolved = (source_path.parent / val).resolve()
            if resolved.exists():
                sql_text = resolved.read_text(encoding="utf-8")
                source_uri = str(resolved)
            else:
                warnings.append(
                    f"sql template file not found: {resolved} (task '{task_id}')"
                )
        else:
            sql_text = val
    elif isinstance(sql_node, ast.List):
        parts: list[str] = []
        dyn = False
        for elt in sql_node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                parts.append(elt.value)
            else:
                warnings.append(
                    f"dynamic sql= expression in Airflow task '{task_id}' at "
                    f"{source_path}:{elt.lineno}"
                )
                dyn = True
                break
        if not dyn and parts:
            sql_text = ";\n".join(s.rstrip(";").strip() for s in parts) + ";"
    else:
        line = getattr(sql_node, "lineno", "?")
        warnings.append(
            f"dynamic sql= expression in Airflow task '{task_id}' at {source_path}:{line}"
        )

    activity_type = _classify(op_name, conn_id, sql_text)
    if conn_id and activity_type == ActivityType.UNSUPPORTED and sql_text is not None:
        warnings.append(
            f"task '{task_id}' conn_id '{conn_id}' does not look like a mssql/azure_sql connection"
        )

    return SqlActivity(
        activity_id=task_id,
        name=task_id,
        activity_type=activity_type,
        sql_text=sql_text,
        source_uri=source_uri,
        env_ref=conn_id,
        order=order,
        depends_on=(),
        warnings=tuple(warnings),
    )


def _looks_like_template_path(val: str) -> bool:
    return val.endswith(".sql") or "/" in val or "\\" in val


def _classify(op_name: str, conn_id: str | None, sql_text: str | None) -> ActivityType:
    if sql_text is None:
        return ActivityType.UNSUPPORTED
    if conn_id is not None and not any(h in conn_id.lower() for h in _MSSQL_CONN_HINTS):
        return ActivityType.UNSUPPORTED
    if op_name == "MsSqlOperator":
        return ActivityType.SQL_QUERY
    return ActivityType.SQL_QUERY


def _map_var_to_task(tree: ast.Module, aliases: dict[str, str]) -> dict[str, str]:
    """Variable name → task_id for every assignment `t = <TargetOperator>(task_id=...)`."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if not _is_target_operator(node.value, aliases):
            continue
        task_id = _literal_str(_get_kw(node.value, "task_id"))
        if task_id is None:
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                out[target.id] = task_id
    return out


def _collect_edges(
    tree: ast.Module, var_to_task: dict[str, str], known: set[str]
) -> list[ActivityEdge]:
    edges: list[ActivityEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.RShift, ast.LShift)):
            _emit_shift(node, var_to_task, edges)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr not in ("set_downstream", "set_upstream") or not node.args:
                continue
            left = _resolve_single(node.func.value, var_to_task)
            if left is None:
                continue
            for r in _collect_targets(node.args[0], var_to_task):
                if attr == "set_downstream":
                    edges.append(ActivityEdge(from_activity_id=left, to_activity_id=r))
                else:
                    edges.append(ActivityEdge(from_activity_id=r, to_activity_id=left))

    seen: set[tuple[str, str]] = set()
    out: list[ActivityEdge] = []
    for e in edges:
        key = (e.from_activity_id, e.to_activity_id)
        if (
            key in seen
            or e.from_activity_id not in known
            or e.to_activity_id not in known
        ):
            continue
        seen.add(key)
        out.append(e)
    return out


def _emit_shift(
    node: ast.BinOp, var_to_task: dict[str, str], edges: list[ActivityEdge]
) -> None:
    is_rshift = isinstance(node.op, ast.RShift)
    lefts = _collect_targets(node.left, var_to_task)
    rights = _collect_targets(node.right, var_to_task)
    for l in lefts:
        for r in rights:
            if is_rshift:
                edges.append(ActivityEdge(from_activity_id=l, to_activity_id=r))
            else:
                edges.append(ActivityEdge(from_activity_id=r, to_activity_id=l))


def _collect_targets(node: ast.expr, var_to_task: dict[str, str]) -> list[str]:
    if isinstance(node, ast.Name):
        t = var_to_task.get(node.id)
        return [t] if t else []
    if isinstance(node, ast.List):
        out: list[str] = []
        for e in node.elts:
            if isinstance(e, ast.Name):
                t = var_to_task.get(e.id)
                if t:
                    out.append(t)
        return out
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.RShift, ast.LShift)):
        edge_side = node.right if isinstance(node.op, ast.RShift) else node.left
        return _collect_targets(edge_side, var_to_task)
    return []


def _resolve_single(node: ast.expr, var_to_task: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return var_to_task.get(node.id)
    return None
