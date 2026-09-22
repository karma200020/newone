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
