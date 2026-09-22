from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ops import model
from ops.config import Settings
from ops.domain import (
    Action,
    ActionKind,
    Diagnosis,
    Evidence,
    Hypothesis,
    Investigation,
    Principal,
    Source,
    Status,
    utcnow,
)
from ops.provenance import validate_provenance


@pytest.fixture
def llm_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "llm_api_key": SecretStr("test-provider-key"),
            "llm_model": "test-mini",
            "llm_escalation_model": "test-large",
        }
    )


def evidence(key: str, source: Source = Source.LOGS, *, suspicious: bool = False) -> Evidence:
    content = f"Stored observation {key}"
    return Evidence(
        id=key,
        source_type=source,
        source_id=f"source-{key}",
        timestamp=utcnow(),
        content=content,
        relevance=0.9,
        tool="test.read",
        content_hash=Evidence.digest(content),
        suspicious=suspicious,
    )


@pytest.fixture
def state(user: Principal) -> Investigation:
    return Investigation(
        tenant_id="acme",
        requested_by=user,
        description="PRIVATE request: checkout-service 5xx",
        scenario_id="INC-001",
        status=Status.DIAGNOSED,
        evidence=[
            evidence("e1"),
            evidence("e2", Source.MONITORING),
            evidence("e3"),
            evidence("unrelated"),
            evidence("quarantined", Source.DOCUMENTATION, suspicious=True),
        ],
        hypotheses=[
            Hypothesis(
                id="deployment",
                title="Deployment regression",
                required_signals=["errors", "change"],
                supporting_evidence=["e1", "e2"],
                score=1,
                test_summary="2/2 tested signals.",
            ),
            Hypothesis(
                id="capacity",
                title="Insufficient capacity",
                required_signals=["saturation"],
                supporting_evidence=["e3"],
                score=0.6,
                test_summary="1/1 tested signal.",
            ),
            Hypothesis(id="untested", title="Untested idea", required_signals=[]),
        ],
        diagnosis=Diagnosis(
            root_cause="Deployment regression",
            hypothesis_id="deployment",
            confidence="high",
            confidence_explanation="Two independent signals.",
            supporting_evidence=["e1", "e2"],
            contradictory_evidence=[],
            uncertainty=[],
        ),
        actions=[
            Action(
                kind=ActionKind.ROLLBACK,
                service="checkout-service",
                reason="Deterministic runbook recommendation",
                evidence_ids=["e1", "e2"],
                parameters={"deployment": "last-known-good"},
                risk="high",
            )
        ],
    )


def provider_response(
    hypothesis: str = "deployment", citations: list[str] | None = None
) -> dict[str, Any]:
    return {
        "model": "test-mini-2026-01-01",
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "hypothesis_id": hypothesis,
                            "supporting_evidence_ids": ["e1"] if citations is None else citations,
                        }
                    )
                },
            }
        ],
    }


async def invoke(
    state: Investigation,
    settings: Settings,
    data: dict[str, Any],
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await model.enrich_with_model(state, settings, client=client)
        assert not client.is_closed
    return requests


def assert_deterministic_preserved(state: Investigation, before: Investigation) -> None:
    assert state.diagnosis is not None and before.diagnosis is not None
    assert state.diagnosis.root_cause == before.diagnosis.root_cause
    assert state.diagnosis.hypothesis_id == before.diagnosis.hypothesis_id
    assert state.diagnosis.supporting_evidence == before.diagnosis.supporting_evidence
    assert state.diagnosis.contradictory_evidence == before.diagnosis.contradictory_evidence
    assert state.actions == before.actions
    assert state.hypotheses == before.hypotheses
    assert state.evidence == before.evidence
    assert state.status == before.status
    validate_provenance(state)


@pytest.mark.parametrize("key", [None, SecretStr("")])
async def test_no_key_is_exact_noop(
    state: Investigation, llm_settings: Settings, key: SecretStr | None
) -> None:
    llm_settings.llm_api_key = key
    before = state.model_dump()

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("No-key mode attempted an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        await model.enrich_with_model(state, llm_settings, client=client)
    assert state.model_dump() == before


async def test_valid_selection_and_actual_token_cost_accumulate(
    state: Investigation, llm_settings: Settings
) -> None:
    before = state.model_copy(deep=True)
    state.usage.input_tokens = 7
    state.usage.output_tokens = 3
    state.usage.cost_usd = 0.02
    state.usage.retrieval_calls = 4
    llm_settings.llm_input_cost_per_million = 3
    llm_settings.llm_output_cost_per_million = 7
    requests = await invoke(state, llm_settings, provider_response())
    assert len(requests) == 1
    assert state.usage.input_tokens == 1007
    assert state.usage.output_tokens == 103
    assert state.usage.cost_usd == pytest.approx(0.0237)
    assert state.usage.retrieval_calls == 4
    assert state.usage.model == "test-mini"
    assert state.usage.model_version == "test-mini-2026-01-01"
    assert state.usage.prompt_version == model.PROMPT_VERSION
    assert not state.warnings
    assert state.trace[-1].summary == model.CORROBORATED
    assert state.trace[-1].evidence_ids == ["e1"]
    assert state.diagnosis == before.diagnosis
    assert_deterministic_preserved(state, before)
    await invoke(state, llm_settings, provider_response())
    assert state.usage.input_tokens == 2007
    assert state.usage.output_tokens == 203
    assert state.usage.cost_usd == pytest.approx(0.0274)


@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
async def test_routing_and_tier_pricing(
    state: Investigation, llm_settings: Settings, confidence: str
) -> None:
    assert state.diagnosis is not None
    state.diagnosis = state.diagnosis.model_copy(update={"confidence": confidence})
    llm_settings.llm_escalation_input_cost_per_million = 13
    llm_settings.llm_escalation_output_cost_per_million = 17
    requests = await invoke(state, llm_settings, provider_response())
    payload = json.loads(requests[0].content)
    escalation = confidence != "high"
    assert payload["model"] == ("test-large" if escalation else "test-mini")
    assert state.usage.cost_usd == pytest.approx(0.0147 if escalation else 0.00056)
    assert state.diagnosis.confidence == confidence


@pytest.mark.parametrize(
    ("hypothesis", "citations"),
    [
        ("imaginary", ["e1"]),
        ("untested", ["e1"]),
        ("deployment", ["missing"]),
        ("deployment", ["quarantined"]),
        ("deployment", ["e3"]),
        ("deployment", ["unrelated"]),
        ("deployment", []),
        ("deployment", ["e1", "e1"]),
        ("abstain", ["e1"]),
    ],
)
async def test_hallucinated_or_invalid_citations_rejected(
    state: Investigation, llm_settings: Settings, hypothesis: str, citations: list[str]
) -> None:
    before = state.model_copy(deep=True)
    await invoke(state, llm_settings, provider_response(hypothesis, citations))
    assert state.warnings == [model.MALFORMED]
    assert state.trace[-1].summary == model.MALFORMED
    assert state.diagnosis is not None and state.diagnosis.confidence == "medium"
    assert state.usage.input_tokens == 1000
    assert_deterministic_preserved(state, before)


@pytest.mark.parametrize("hypothesis", ["capacity", "abstain"])
@pytest.mark.parametrize(
    ("confidence", "reduced"), [("high", "medium"), ("medium", "low"), ("low", "low")]
)
async def test_disagreement_and_abstention_only_reduce_confidence(
    state: Investigation,
    llm_settings: Settings,
    hypothesis: str,
    confidence: str,
    reduced: str,
) -> None:
    assert state.diagnosis is not None
    state.diagnosis = state.diagnosis.model_copy(update={"confidence": confidence})
    before = state.model_copy(deep=True)
    await invoke(
        state,
        llm_settings,
        provider_response(hypothesis, ["e3"] if hypothesis == "capacity" else []),
    )
    expected = model.DISAGREED if hypothesis == "capacity" else model.ABSTAINED
    assert state.warnings == [expected]
    assert state.diagnosis.confidence == reduced
    assert expected in state.diagnosis.uncertainty
    assert expected in state.diagnosis.confidence_explanation
    assert_deterministic_preserved(state, before)


async def test_selection_cannot_resolve_inconclusive_diagnosis(
    state: Investigation, llm_settings: Settings
) -> None:
    assert state.diagnosis is not None
    state.diagnosis.hypothesis_id = None
    state.diagnosis.root_cause = "Insufficient evidence."
    state.diagnosis.confidence = "low"
    state.diagnosis.supporting_evidence = []
    before = state.model_copy(deep=True)
    await invoke(state, llm_settings, provider_response())
    assert state.warnings == [model.DISAGREED]
    assert state.diagnosis.confidence == "low"
    assert_deterministic_preserved(state, before)


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '["deployment"]',
        '{"hypothesis_id":"deployment","supporting_evidence_ids":["e1"],"reasoning":"EVIL"}',
        '{"hypothesis_id":"deployment","supporting_evidence_ids":["e1"],"actions":["EVIL"]}',
        '{"hypothesis_id":"deployment","supporting_evidence_ids":["e1"],"parameters":{"x":"EVIL"}}',
        '{"hypothesis_id":"deployment","supporting_evidence_ids":["e1"],"root_cause":"EVIL"}',
        '{"hypothesis_id":"deployment","hypothesis_id":"capacity","supporting_evidence_ids":["e3"]}',
    ],
)
async def test_free_text_new_claims_and_duplicate_fields_rejected(
    state: Investigation, llm_settings: Settings, content: str
) -> None:
    data = provider_response()
    data["choices"][0]["message"]["content"] = content
    before = state.model_copy(deep=True)
    await invoke(state, llm_settings, data)
    assert state.warnings == [model.MALFORMED]
    assert "EVIL" not in state.model_dump_json()
    assert_deterministic_preserved(state, before)


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
@pytest.mark.parametrize("value", [-1, True, 1.5, "100", None, 1_000_001])
async def test_usage_requires_bounded_nonnegative_integers(
    state: Investigation, llm_settings: Settings, field: str, value: Any
) -> None:
    data = provider_response()
    data["usage"][field] = value
    state.usage.input_tokens = 8
    state.usage.output_tokens = 9
    state.usage.cost_usd = 0.25
    await invoke(state, llm_settings, data)
    assert state.warnings == [model.MALFORMED]
    assert state.usage.input_tokens == 8
    assert state.usage.output_tokens == 9
    assert state.usage.cost_usd == 0.25
    assert state.usage.model_version == "unavailable"


@pytest.mark.parametrize("variant", ["missing", "excess_completion", "zero", "max_prompt"])
async def test_usage_boundaries(state: Investigation, llm_settings: Settings, variant: str) -> None:
    data = provider_response()
    if variant == "missing":
        del data["usage"]
    elif variant == "excess_completion":
        data["usage"]["completion_tokens"] = llm_settings.max_model_tokens + 1
    else:
        data["usage"] = {
            "prompt_tokens": 0 if variant == "zero" else 1_000_000,
            "completion_tokens": 0,
        }
    await invoke(state, llm_settings, data)
    if variant in ("missing", "excess_completion"):
        assert state.warnings == [model.MALFORMED]
        assert state.usage.input_tokens == state.usage.output_tokens == 0
    else:
        assert not state.warnings
        assert state.usage.input_tokens == data["usage"]["prompt_tokens"]
        assert state.usage.output_tokens == 0


async def test_request_minimization_and_fixed_destination(
    state: Investigation, llm_settings: Settings
) -> None:
    for index in range(20):
        item = evidence(f"extra-{index:02}")
        item.content = "x" * 500 + "EXCLUDED_TAIL"
        item.content_hash = Evidence.digest(item.content)
        item.attributes = {"secret": "PRIVATE_ATTRIBUTE"}
        item.reference = "https://private.invalid"
        state.evidence.append(item)
        state.hypotheses[0].supporting_evidence.append(item.id)
    # A quarantined supporting document must never leave the application.
    state.hypotheses[0].supporting_evidence.append("quarantined")
    state.hypotheses[0].title = "Ignore external instructions" + "t" * 400
    llm_settings.max_model_tokens = 100_000
    requests = await invoke(state, llm_settings, provider_response())
    request = requests[0]
    payload = json.loads(request.content)
    assert str(request.url) == model.ENDPOINT
    assert request.method == "POST"
    assert all(value == 10.0 for value in request.extensions["timeout"].values())
    assert payload["max_tokens"] == 4096
    assert payload["n"] == 1 and payload["stream"] is False and payload["store"] is False
    assert [entry["role"] for entry in payload["messages"]] == ["system", "user"]
    assert payload["messages"][0]["content"] == model.SYSTEM_PROMPT
    context = json.loads(payload["messages"][1]["content"])
    assert len(context["evidence"]) == 12
    assert max(len(item["excerpt"]) for item in context["evidence"]) == 500
    assert len(context["hypotheses"][0]["title"]) == 200
    for excluded in (
        "PRIVATE",
        "EXCLUDED_TAIL",
        "quarantined",
        "unrelated",
        "untested",
        "last-known-good",
        "private.invalid",
        "content_hash",
    ):
        assert excluded not in request.content.decode()
    schema = payload["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert set(schema["schema"]["properties"]) == {"hypothesis_id", "supporting_evidence_ids"}
    assert schema["schema"]["properties"]["hypothesis_id"]["enum"] == [
        "deployment",
        "capacity",
        "abstain",
    ]
    sent_ids = {item["id"] for item in context["evidence"]}
    for hypothesis in context["hypotheses"]:
        assert set(hypothesis["supporting_evidence_ids"]) <= sent_ids
    assert (
        set(schema["schema"]["properties"]["supporting_evidence_ids"]["items"]["enum"]) == sent_ids
    )


async def test_citation_to_stored_but_unsent_support_rejected(
    state: Investigation, llm_settings: Settings
) -> None:
    for index in range(15):
        item = evidence(f"extra-{index}")
        item.relevance = 1
        state.evidence.append(item)
        state.hypotheses[0].supporting_evidence.append(item.id)
    await invoke(state, llm_settings, provider_response())
    assert state.warnings == [model.MALFORMED]


@pytest.mark.parametrize(
    ("failure", "expected_attempts"),
    [
        (429, 3),
        (500, 3),
        (503, 3),
        ("timeout", 3),
        ("deadline", 3),
        (400, 1),
        (401, 1),
        (403, 1),
        (302, 1),
        (307, 1),
        ("connection", 1),
    ],
)
async def test_failure_retry_bounds_and_redirects(
    state: Investigation,
    llm_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    failure: int | str,
    expected_attempts: int,
) -> None:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(model.asyncio, "sleep", sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE failure", request=request)
        if failure == "deadline":
            raise TimeoutError("PRIVATE failure")
        if failure == "connection":
            raise httpx.ConnectError("PRIVATE failure", request=request)
        assert isinstance(failure, int)
        return httpx.Response(
            failure, headers={"Location": "https://untrusted.invalid"}, text="PRIVATE failure"
        )

    before = state.model_copy(deep=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        await model.enrich_with_model(state, llm_settings, client=client)
    assert len(requests) == expected_attempts
    assert sleeps == ([0.5, 1.0] if expected_attempts == 3 else [])
    assert all(str(request.url) == model.ENDPOINT for request in requests)
    assert state.warnings == [model.FAILED]
    assert state.trace[-1].summary == model.FAILED
    assert not any(event.summary == model.CORROBORATED for event in state.trace)
    assert state.usage.input_tokens == state.usage.output_tokens == 0
    assert state.usage.cost_usd == 0
    assert state.usage.model == "test-mini"
    assert state.usage.model_version == "unavailable"
    assert state.diagnosis is not None and state.diagnosis.confidence == "medium"
    assert "PRIVATE failure" not in state.model_dump_json()
    assert_deterministic_preserved(state, before)


async def test_transient_failure_then_success_counts_only_actual_usage(
    state: Investigation, llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(model.asyncio, "sleep", sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return (
            httpx.Response(429) if attempts < 3 else httpx.Response(200, json=provider_response())
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await model.enrich_with_model(state, llm_settings, client=client)
    assert attempts == 3 and sleeps == [0.5, 1.0]
    assert not state.warnings
    assert state.usage.input_tokens == 1000
    assert state.usage.output_tokens == 100
    assert state.trace[-1].summary == model.CORROBORATED


@pytest.mark.parametrize(
    "variant", ["refusal", "truncated", "tool", "function", "choices", "model"]
)
async def test_invalid_provider_envelope(
    state: Investigation, llm_settings: Settings, variant: str
) -> None:
    data = provider_response()
    if variant == "refusal":
        data["choices"][0]["message"]["refusal"] = "PRIVATE refusal"
    elif variant == "truncated":
        data["choices"][0]["finish_reason"] = "length"
    elif variant == "tool":
        data["choices"][0]["message"]["tool_calls"] = [{"name": "PRIVATE action"}]
    elif variant == "function":
        data["choices"][0]["message"]["function_call"] = {"name": "PRIVATE action"}
    elif variant == "choices":
        data["choices"] = []
    else:
        data["model"] = "PRIVATE arbitrary instructions\n"
    await invoke(state, llm_settings, data)
    assert state.warnings == [model.MALFORMED]
    assert state.usage.input_tokens == 1000
    assert "PRIVATE refusal" not in state.model_dump_json()
    assert "PRIVATE action" not in state.model_dump_json()
    assert "PRIVATE arbitrary" not in state.model_dump_json()


@pytest.mark.parametrize(
    "body",
    [b"not JSON", b"\xff", b"x" * 65_537, b"null"],
    ids=["invalid-json", "invalid-encoding", "oversized", "not-object"],
)
async def test_invalid_or_oversized_http_body(
    state: Investigation, llm_settings: Settings, body: bytes
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await model.enrich_with_model(state, llm_settings, client=client)
    assert requests == 1
    assert state.warnings == [model.MALFORMED]
    assert state.usage.cost_usd == 0


async def test_wall_clock_deadline_cancels_stalled_request(
    state: Investigation, llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = 0
    cancelled = 0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests, cancelled
        requests += 1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled += 1
        pytest.fail("Stalled request must be cancelled")

    monkeypatch.setattr(model, "ATTEMPT_SECONDS", 0.005)
    monkeypatch.setattr(model.asyncio, "sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await model.enrich_with_model(state, llm_settings, client=client)
    assert requests == cancelled == 3
    assert sleeps == [0.5, 1.0]
    assert state.warnings == [model.FAILED]
    assert state.usage.input_tokens == state.usage.output_tokens == 0


@pytest.mark.parametrize("count", [32, 33])
async def test_hypothesis_count_boundary(
    state: Investigation, llm_settings: Settings, count: int
) -> None:
    state.hypotheses = [h for h in state.hypotheses if h.test_summary]
    state.hypotheses.extend(
        Hypothesis(
            id=f"tested-{index}",
            title="Another tested hypothesis",
            required_signals=[],
            test_summary="Tested with no supporting signals",
        )
        for index in range(count - len(state.hypotheses))
    )
    requests = await invoke(state, llm_settings, provider_response())
    assert len(requests) == (1 if count == 32 else 0)
    assert state.warnings == ([] if count == 32 else [model.SKIPPED])


@pytest.mark.parametrize("length", [128, 129])
async def test_identifier_length_boundary(
    state: Investigation, llm_settings: Settings, length: int
) -> None:
    state.hypotheses[1].id = "h" * length
    requests = await invoke(state, llm_settings, provider_response())
    assert len(requests) == (1 if length == 128 else 0)
    assert state.warnings == ([] if length == 128 else [model.SKIPPED])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s, c: setattr(s, "diagnosis", None),
        lambda s, c: setattr(s, "hypotheses", []),
        lambda s, c: setattr(s, "evidence", []),
        lambda s, c: setattr(s.evidence[0], "content", "tampered"),
        lambda s, c: s.evidence.append(s.evidence[0]),
        lambda s, c: s.hypotheses.append(s.hypotheses[0]),
        lambda s, c: setattr(c, "llm_input_cost_per_million", -1),
        lambda s, c: setattr(c, "llm_output_cost_per_million", float("inf")),
        lambda s, c: setattr(c, "llm_input_cost_per_million", float("nan")),
        lambda s, c: setattr(c, "max_model_tokens", 0),
        lambda s, c: setattr(c, "llm_model", "model\nexternal instructions"),
    ],
)
async def test_invalid_prerequisites_skip_without_network(
    state: Investigation,
    llm_settings: Settings,
    mutate: Callable[[Investigation, Settings], Any],
) -> None:
    mutate(state, llm_settings)

    def unexpected(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid prerequisites must not send evidence")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        await model.enrich_with_model(state, llm_settings, client=client)
    assert state.warnings == [model.SKIPPED]
    assert state.trace[-1].summary == model.SKIPPED
    assert state.usage.model == "deterministic-evidence-engine"


async def test_owned_client_uses_safe_defaults_and_closes(
    state: Investigation, llm_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx.AsyncClient
    clients: list[httpx.AsyncClient] = []

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert kwargs["timeout"] == model.TIMEOUT
        client = real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=provider_response())
            ),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(model.httpx, "AsyncClient", factory)
    await model.enrich_with_model(state, llm_settings)
    assert len(clients) == 1 and clients[0].is_closed
    assert state.trace[-1].summary == model.CORROBORATED
