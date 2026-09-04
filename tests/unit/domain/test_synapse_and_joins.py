from __future__ import annotations

from dacpactools.domain.enums import SynapseDistributionKind, SynapseIndexKind
from dacpactools.domain.identifiers import ObjectRef
from dacpactools.domain.joins import JoinPair, JoinReport, JoinUsage
from dacpactools.domain.lineage import LineageNode, SynapseTableSpec


def _ref(name: str) -> ObjectRef:
    return ObjectRef(database=None, schema="TD_HLP", name=name)


def test_synapse_spec_defaults() -> None:
    spec = SynapseTableSpec(
        distribution_kind=SynapseDistributionKind.HASH,
        distribution_columns=("PARTYROLEID",),
        index_kind=SynapseIndexKind.CLUSTERED_COLUMNSTORE,
    )
    node = LineageNode(ref=_ref("DMS_ADDRESSR"), synapse=spec)
    assert node.synapse is spec
    assert node.synapse.distribution_kind is SynapseDistributionKind.HASH
    assert node.synapse.source == "CTAS"


def test_join_report_shape() -> None:
    target = ObjectRef(database=None, schema="sys", name="types")
    other = ObjectRef(database=None, schema="sys", name="columns")
    consumer = ObjectRef(database=None, schema="dbo", name="usp_X")
    usage = JoinUsage(
        consumer=consumer,
        target=target,
        other=other,
        join_type="INNER",
        pairs=(JoinPair(target_column="system_type_id", other_column="system_type_id"),),
        on_expression="c.system_type_id = sty.system_type_id",
    )
    report = JoinReport(target=target, usages=(usage,))
    assert report.usages[0].join_type == "INNER"
    assert report.usages[0].pairs[0].target_column == "system_type_id"
