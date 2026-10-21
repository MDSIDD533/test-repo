from app.agents import PIPELINE_STAGES, run_agent_pipeline


def complete_agents(*, calibrated=True, action="BUY", probability=0.72):
    agents = {}
    for stage in PIPELINE_STAGES:
        agents[stage] = lambda context, name=stage: {"stage": name}
    agents["prediction"] = lambda context: {
        "calibrated": calibrated,
        "probability_tp_before_sl": probability,
        "horizon": "4h",
        "sample_size": 250,
    }
    agents["risk_decision"] = lambda context: {"action": action}
    agents["timetraveler"] = lambda context: {
        "simulation_count": context.get("requested_simulations", 100),
        "seed": 1234,
        "assumptions": ["test fixture only"],
    }
    return agents


def test_pipeline_runs_all_agents_in_defined_order():
    report = run_agent_pipeline(
        "btcusdt", complete_agents(), run_id="test-run", as_of="2026-01-01T00:00:00Z"
    )
    assert report.symbol == "BTCUSDT"
    assert report.run_id == "test-run"
    assert report.status == "completed"
    assert report.action == "BUY"
    assert [stage.stage for stage in report.stages] == list(PIPELINE_STAGES)
    assert all(stage.status == "completed" for stage in report.stages)


def test_pipeline_forces_no_trade_when_probability_is_not_calibrated():
    report = run_agent_pipeline("ETHUSDT", complete_agents(calibrated=False))
    assert report.status == "completed_no_trade"
    assert report.action == "NO TRADE"
    assert any("not demonstrably calibrated" in warning for warning in report.warnings)


def test_pipeline_forces_no_trade_for_invalid_probability():
    report = run_agent_pipeline("ETHUSDT", complete_agents(probability=1.5))
    assert report.action == "NO TRADE"
    assert report.status == "completed_no_trade"


def test_pipeline_stops_after_missing_agent_without_inventing_results():
    agents = complete_agents()
    del agents["research"]
    report = run_agent_pipeline("SOLUSDT", agents)
    assert report.status == "incomplete"
    assert report.action == "NO TRADE"
    assert [stage.stage for stage in report.stages] == ["market_data", "market_analysis", "timetraveler", "research"]
    assert report.stages[-1].status == "not_configured"
    assert report.stages[2].stage == "timetraveler"
    assert report.stages[2].status == "completed"


def test_pipeline_stops_after_agent_exception():
    agents = complete_agents()
    agents["market_analysis"] = lambda context: (_ for _ in ()).throw(RuntimeError("fixture failure"))
    report = run_agent_pipeline("SOLUSDT", agents)
    assert report.status == "failed"
    assert report.action == "NO TRADE"
    assert report.stages[-1].stage == "market_analysis"
    assert report.stages[-1].status == "failed"


def test_pipeline_rejects_blank_symbol():
    report = run_agent_pipeline("   ", complete_agents())
    assert report.status == "invalid_request"
    assert report.action == "NO TRADE"

def test_pipeline_passes_requested_simulation_count_to_timetraveler():
    report = run_agent_pipeline("BTCUSDT", complete_agents(), simulation_count=1000)
    assert report.status == "completed"
    time_traveler = next(stage for stage in report.stages if stage.stage == "timetraveler")
    assert time_traveler.result["simulation_count"] == 1000


def test_pipeline_rejects_unsupported_simulation_count():
    try:
        run_agent_pipeline("BTCUSDT", complete_agents(), simulation_count=250)
    except ValueError as exc:
        assert "100, 500, or 1000" in str(exc)
    else:
        raise AssertionError("Unsupported simulation count should be rejected")

