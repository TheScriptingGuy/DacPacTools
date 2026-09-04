from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.joins import JoinPair, JoinUsage
from dacpactools.infrastructure.scriptdom.ast_utils import (
    identifier_value,
    multipart_parts,
    schema_object_parts,
    script_text,
    type_name,
)


@dataclass(frozen=True)
class _NamedTable:
    ref: ObjectRef
    alias: str | None


def find_joins_for(
    consumer: ObjectRef, fragment: Any, target: ObjectRef
) -> list[JoinUsage]:
    """Walk a parsed script (via ScriptDom's own TSqlFragmentVisitor) and return
    every QualifiedJoin whose left OR right side is the target table."""
    if fragment is None:
        return []
    out: list[JoinUsage] = []
    _visit_via_scriptdom(fragment, consumer, target, out)
    return out


_VISITOR_CLASS: Any = None


def _get_visitor_class() -> Any:
    global _VISITOR_CLASS
    if _VISITOR_CLASS is not None:
        return _VISITOR_CLASS
    from Microsoft.SqlServer.TransactSql.ScriptDom import (  # type: ignore  # noqa: PLC0415
        TSqlFragmentVisitor,
    )

    class _JoinVisitor(TSqlFragmentVisitor):  # type: ignore[misc, valid-type]
        __namespace__ = "Dacpactools.Scriptdom"

        def __init__(self) -> None:
            super().__init__()
            self.consumer: ObjectRef | None = None
            self.target: ObjectRef | None = None
            self.out: list[JoinUsage] | None = None

        def Visit(self, node):  # noqa: N802
            if type_name(node) == "QualifiedJoin" and self.out is not None:
                _emit_join(node, self.consumer, self.target, self.out)  # type: ignore[arg-type]

    _VISITOR_CLASS = _JoinVisitor
    return _VISITOR_CLASS


def _visit_via_scriptdom(
    fragment: Any, consumer: ObjectRef, target: ObjectRef, out: list[JoinUsage]
) -> None:
    cls = _get_visitor_class()
    v = cls()
    v.consumer = consumer
    v.target = target
    v.out = out
    fragment.Accept(v)


def _emit_join(
    join_node: Any, consumer: ObjectRef, target: ObjectRef, out: list[JoinUsage]
) -> None:
    tables = _flatten_named_tables(join_node.FirstTableReference) + _flatten_named_tables(
        join_node.SecondTableReference
    )
    if not tables:
        return
    target_key = (target.schema_name.lower(), target.name.lower())
    tgt_sides = [
        t
        for t in tables
        if (t.ref.schema_name.lower(), t.ref.name.lower()) == target_key
    ]
    if not tgt_sides:
        return

    join_type = _join_type_str(getattr(join_node, "QualifiedJoinType", None))
    on_expr = script_text(getattr(join_node, "SearchCondition", None))
    pairs_raw = _equality_pairs(getattr(join_node, "SearchCondition", None))

    # Index tables by their lookup keys (alias + bare name, lowercased).
    by_key: dict[str, _NamedTable] = {}
    for t in tables:
        for k in _keys_for(t):
            by_key.setdefault(k, t)

    for tgt in tgt_sides:
        tgt_keys = _keys_for(tgt)
        # (other-table, list-of-pairs) — grouped
        groups: dict[str, tuple[_NamedTable, list[JoinPair]]] = {}
        for left, right, _ in pairs_raw:
            pair, other_tbl = _assign_pair_with_other(left, right, tgt_keys, by_key)
            if pair is None or other_tbl is None:
                continue
            if (other_tbl.ref.schema_name.lower(), other_tbl.ref.name.lower()) == target_key:
                continue  # self-join mirror
            gkey = f"{other_tbl.ref.schema_name.lower()}.{other_tbl.ref.name.lower()}|{other_tbl.alias or ''}"
            if gkey not in groups:
                groups[gkey] = (other_tbl, [])
            if pair not in groups[gkey][1]:
                groups[gkey][1].append(pair)

        if groups:
            for other, pairs in groups.values():
                out.append(
                    JoinUsage(
                        consumer=consumer,
                        target=tgt.ref,
                        other=other.ref,
                        join_type=join_type,
                        target_alias=tgt.alias,
                        other_alias=other.alias,
                        pairs=tuple(pairs),
                        on_expression=on_expr,
                    )
                )
            continue

        # No attributable equality pairs — emit one usage per named other-side
        # table so the caller still learns *where* the join happens.
        for other in tables:
            if other is tgt:
                continue
            if (other.ref.schema_name.lower(), other.ref.name.lower()) == target_key:
                continue
            out.append(
                JoinUsage(
                    consumer=consumer,
                    target=tgt.ref,
                    other=other.ref,
                    join_type=join_type,
                    target_alias=tgt.alias,
                    other_alias=other.alias,
                    pairs=(),
                    on_expression=on_expr,
                )
            )


def _flatten_named_tables(table_ref: Any) -> list[_NamedTable]:
    if table_ref is None:
        return []
    t = type_name(table_ref)
    if t == "NamedTableReference":
        db, schema, name = schema_object_parts(table_ref.SchemaObject)
        if not name:
            return []
        alias = identifier_value(getattr(table_ref, "Alias", None))
        return [
            _NamedTable(
                ref=ObjectRef(database=db, schema=schema or "dbo", name=name),
                alias=alias,
            )
        ]
    if t in {"QualifiedJoin", "UnqualifiedJoin"}:
        return _flatten_named_tables(table_ref.FirstTableReference) + _flatten_named_tables(
            table_ref.SecondTableReference
        )
    return []


def _join_type_str(qjt: Any) -> str:
    if qjt is None:
        return "JOIN"
    s = str(qjt)
    if s.endswith("Inner") or s == "Inner":
        return "INNER"
    if s.endswith("LeftOuter") or s == "LeftOuter":
        return "LEFT"
    if s.endswith("RightOuter") or s == "RightOuter":
        return "RIGHT"
    if s.endswith("FullOuter") or s == "FullOuter":
        return "FULL"
    return s


def _equality_pairs(cond: Any) -> list[tuple[tuple[str | None, str], tuple[str | None, str], str]]:
    """Extract (prefix, col)=(prefix, col) tuples under AND-joined conditions."""
    out: list[tuple[tuple[str | None, str], tuple[str | None, str], str]] = []
    _walk_cond(cond, out)
    return out


def _walk_cond(
    node: Any,
    out: list[tuple[tuple[str | None, str], tuple[str | None, str], str]],
) -> None:
    if node is None:
        return
    t = type_name(node)
    if t == "BooleanBinaryExpression":
        op = str(getattr(node, "BinaryExpressionType", ""))
        if "And" in op:
            _walk_cond(node.FirstExpression, out)
            _walk_cond(node.SecondExpression, out)
        return
    if t == "BooleanParenthesisExpression":
        _walk_cond(node.Expression, out)
        return
    if t == "BooleanComparisonExpression":
        if "Equals" in str(getattr(node, "ComparisonType", "")):
            left = _col_parts(node.FirstExpression)
            right = _col_parts(node.SecondExpression)
            if left and right:
                out.append((left, right, script_text(node) or ""))
        return


def _col_parts(expr: Any) -> tuple[str | None, str] | None:
    if expr is None or type_name(expr) != "ColumnReferenceExpression":
        return None
    parts = multipart_parts(getattr(expr, "MultiPartIdentifier", None))
    if not parts:
        return None
    col = parts[-1]
    prefix = parts[-2] if len(parts) >= 2 else None
    return (prefix, col)


def _match_pairs_for_sides(
    raw_pairs: list[tuple[tuple[str | None, str], tuple[str | None, str], str]],
    tgt: _NamedTable,
    other: _NamedTable,
) -> list[JoinPair]:
    """For each `a.col = b.col` pair, assign to target/other based on prefix
    (alias or table name)."""
    tgt_keys = _keys_for(tgt)
    other_keys = _keys_for(other)
    out: list[JoinPair] = []
    seen: set[tuple[str, str]] = set()
    for left, right, _ in raw_pairs:
        pair = _assign_pair(left, right, tgt_keys, other_keys)
        if pair is None:
            continue
        key = (pair.target_column.lower(), pair.other_column.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(pair)
    return out


def _keys_for(t: _NamedTable) -> set[str]:
    keys: set[str] = set()
    if t.alias:
        keys.add(t.alias.lower())
    keys.add(t.ref.name.lower())
    return keys


def _assign_pair_with_other(
    left: tuple[str | None, str],
    right: tuple[str | None, str],
    tgt_keys: set[str],
    by_key: dict[str, _NamedTable],
) -> tuple[JoinPair | None, _NamedTable | None]:
    l_prefix, l_col = left
    r_prefix, r_col = right
    lp = (l_prefix or "").lower()
    rp = (r_prefix or "").lower()
    if lp in tgt_keys and rp in by_key and rp not in tgt_keys:
        return JoinPair(target_column=l_col, other_column=r_col), by_key[rp]
    if rp in tgt_keys and lp in by_key and lp not in tgt_keys:
        return JoinPair(target_column=r_col, other_column=l_col), by_key[lp]
    return None, None
