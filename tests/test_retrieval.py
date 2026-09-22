from pathlib import Path

import pytest

from ops.retrieval import HybridRetriever
from ops.synthetic import runbooks


def test_hybrid_rollback_retrieval_and_metadata(tmp_path: Path) -> None:
    records = runbooks("checkout-service") + runbooks("payment-service")
    index = HybridRetriever(records)
    results = index.search(
        "How should checkout-service be rolled back? rollback recent release",
        service="checkout-service",
        tenant_id="acme",
        metadata={"document_type": "runbook"},
        limit=3,
    )
    assert results and "deployment-regression" in results[0].record.source_id
    assert all(result.record.service == "checkout-service" for result in results)
    assert not index.search("rollback", service="checkout-service", tenant_id="different")
    assert not index.search(
        "rollback",
        service="checkout-service",
        tenant_id="acme",
        metadata={"environment": "production"},
    )
    path = tmp_path / "index.json"
    index.save(path)
    loaded = HybridRetriever.load(path)
    assert loaded.search("rollback", service="checkout-service", tenant_id="acme")


def test_chunk_provenance_and_integrity(tmp_path: Path) -> None:
    record = runbooks("checkout-service")[0]
    record.content = "Rollback the release safely. " * 100
    index = HybridRetriever([record], chunk_size=200, overlap=30)
    assert len(index.chunks) > 10
    assert all(chunk.record.source_id == record.source_id for chunk in index.chunks)
    path = tmp_path / "index.json"
    index.save(path)
    path.write_text(path.read_text().replace("Rollback the release", "Tampered the release"))
    with pytest.raises(ValueError, match="integrity"):
        HybridRetriever.load(path)


async def test_ingested_index_is_used_by_documentation_tools(tmp_path: Path) -> None:
    from ops.domain import Principal
    from ops.tools import SyntheticProvider, ToolGateway, ToolInput

    document = runbooks("checkout-service")[0]
    document.source_id = "operator-ingested-runbook-v2"
    index_path = tmp_path / "ingested.json"
    HybridRetriever([document]).save(index_path)
    user = Principal(
        subject="operator", tenant_id="acme", role="operator", services=["checkout-service"]
    )
    result = await ToolGateway(SyntheticProvider(index_path)).call(
        "documentation.search",
        ToolInput(service="checkout-service", scenario_id="INC-001", query="rollback release"),
        user,
    )
    assert not result.error
    assert {item.source_id for item in result.evidence} == {"operator-ingested-runbook-v2"}
