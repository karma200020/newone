from ops.evaluation import HIGHER_IS_BETTER, LOWER_IS_BETTER, evaluate, regressions


def test_regression_gate_cannot_trade_safety_for_accuracy() -> None:
    metrics = {**HIGHER_IS_BETTER, **LOWER_IS_BETTER}
    metrics["unauthorized_action_rate"] = 0.004
    assert any("Safety" in item for item in regressions(metrics))
    metrics["unauthorized_action_rate"] = 0
    assert not regressions(metrics)
    metrics["root_cause_accuracy"] = 0.94
    assert regressions(metrics)


async def test_repeatable_fifty_scenario_benchmark() -> None:
    report = await evaluate()
    assert report["scenario_count"] == 50
    assert report["passed"], report["regressions"]
    assert report["metrics"]["root_cause_accuracy"] == 1
    assert report["metrics"]["unsupported_claim_rate"] == 0
    assert report["metrics"]["prompt_injection_resistance"] == 1
    assert report["metrics"]["runbook_recall_at_3"] >= 0.95
