from ops.domain import Source
from ops.synthetic import SERVICES, generate_scenarios


def test_fifty_deterministic_multisource_incidents() -> None:
    scenarios = generate_scenarios()
    assert len(scenarios) == 50
    assert len({s.ground_truth for s in scenarios}) == 10
    assert {s.service for s in scenarios} == set(SERVICES)
    assert [s.model_dump() for s in scenarios] == [s.model_dump() for s in generate_scenarios()]
    for scenario in scenarios:
        assert set(scenario.expected_evidence) <= {r.source_id for r in scenario.records}
        assert len({r.source for r in scenario.records}) == len(Source)
        assert all(r.tenant_id == "acme" for r in scenario.records)
        assert all(scenario.ground_truth not in r.attributes for r in scenario.records)


def test_tenant_specific_fixtures() -> None:
    assert all(
        r.tenant_id == "tenant-b"
        for scenario in generate_scenarios("tenant-b")
        for r in scenario.records
    )
