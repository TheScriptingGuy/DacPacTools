from __future__ import annotations

import pytest

from dacpactools.domain.enums import SqlTargetPlatform


@pytest.mark.parametrize(
    ("edition", "expected"),
    [
        (2, SqlTargetPlatform.SQL_SERVER),
        (3, SqlTargetPlatform.SQL_SERVER),
        (4, SqlTargetPlatform.SQL_SERVER),
        (5, SqlTargetPlatform.AZURE_SQL_DB),
        (6, SqlTargetPlatform.SYNAPSE_DEDICATED_POOL),
        (8, SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE),
        (9, SqlTargetPlatform.SQL_SERVER),
        (11, SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL),
        (999, SqlTargetPlatform.UNKNOWN),
        (None, SqlTargetPlatform.UNKNOWN),
    ],
)
def test_from_engine_edition(edition: int | None, expected: SqlTargetPlatform) -> None:
    assert SqlTargetPlatform.from_engine_edition(edition) is expected


def test_is_mpp_only_for_dedicated_pool() -> None:
    assert SqlTargetPlatform.SYNAPSE_DEDICATED_POOL.is_mpp
    for p in SqlTargetPlatform:
        if p is not SqlTargetPlatform.SYNAPSE_DEDICATED_POOL:
            assert not p.is_mpp


def test_supports_included_columns() -> None:
    assert SqlTargetPlatform.SQL_SERVER.supports_included_columns
    assert SqlTargetPlatform.AZURE_SQL_DB.supports_included_columns
    assert SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE.supports_included_columns
    assert not SqlTargetPlatform.SYNAPSE_DEDICATED_POOL.supports_included_columns
    assert not SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL.supports_included_columns


def test_supports_persistent_indexes() -> None:
    assert not SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL.supports_persistent_indexes
    assert not SqlTargetPlatform.UNKNOWN.supports_persistent_indexes
    assert SqlTargetPlatform.SQL_SERVER.supports_persistent_indexes
    assert SqlTargetPlatform.SYNAPSE_DEDICATED_POOL.supports_persistent_indexes


def test_supports_columnstore() -> None:
    for p in (
        SqlTargetPlatform.SQL_SERVER,
        SqlTargetPlatform.AZURE_SQL_DB,
        SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE,
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
    ):
        assert p.supports_columnstore
    assert not SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL.supports_columnstore
    assert not SqlTargetPlatform.FABRIC_WAREHOUSE.supports_columnstore
    assert not SqlTargetPlatform.UNKNOWN.supports_columnstore


def test_fabric_warehouse_capabilities() -> None:
    p = SqlTargetPlatform.FABRIC_WAREHOUSE
    assert p.is_fabric
    assert p.is_stats_only
    assert not p.supports_persistent_indexes
    assert not p.supports_included_columns
    assert not p.supports_filtered_index
    assert not p.is_mpp
    assert not p.is_serverless


def test_stats_only_flag_covers_serverless_and_fabric() -> None:
    assert SqlTargetPlatform.SYNAPSE_SERVERLESS_POOL.is_stats_only
    assert SqlTargetPlatform.FABRIC_WAREHOUSE.is_stats_only
    for p in (
        SqlTargetPlatform.SQL_SERVER,
        SqlTargetPlatform.AZURE_SQL_DB,
        SqlTargetPlatform.AZURE_SQL_MANAGED_INSTANCE,
        SqlTargetPlatform.SYNAPSE_DEDICATED_POOL,
    ):
        assert not p.is_stats_only
