from app.agents import PIPELINE_STAGES, run_agent_pipeline
from app.report_agent import run_report_agent


def complete_agents(*, calibrated=True, action="BUY", probability=0.72):
    agents = {}
    for stage in PIPELINE_STAGES:
        agents[stage] = lambda context, name=stage: {"stage": name}
    agents["prediction"] = lambda context: {
        "symbol": context["symbol"],
        "interval": "5m",
        "forecast_created_at": context["as_of"],
        "horizon_seconds": 300,
        "reference_price": 65000.0,
        "direction": "UP",
        "calibrated": calibrated,
        "probability_tp_before_sl": probability,
        "model_version": "test_rule_v1",
    }
    agents["risk_decision"] = lambda context: {
        "action": action,
        "authorized": action != "NO TRADE",
    }
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
    prediction_stage = next(stage for stage in report.stages if stage.stage == "prediction")
    assert prediction_stage.result["forecast_ledger_id"]
    assert prediction_stage.result["forecast_target_time"] == "2026-01-01T00:05:00+00:00"


def test_pipeline_freezes_forecast_before_the_report_stage_runs():
    from app.forecast_ledger import read_ledger

    seen_at_report = {}
    agents = complete_agents()
    report_agent = agents["report"]

    def report_spy(context):
        seen_at_report.update(read_ledger())
        return report_agent(context)

    agents["report"] = report_spy
    report = run_agent_pipeline(
        "BTCUSDT", agents, run_id="ledger-order", as_of="2026-01-01T00:00:00Z"
    )

    assert report.status == "completed"
    assert seen_at_report["summary"]["total"] == 1
    frozen = seen_at_report["forecasts"][0]
    assert frozen["run_id"] == "ledger-order"
    assert frozen["status"] == "pending"
    assert frozen["resolution"] is None
    assert frozen["calibrated"] is True


def test_pipeline_surfaces_a_ledger_failure_instead_of_silently_dropping_the_forecast(monkeypatch):
    import app.agents as agents_module

    def broken_ledger(*args, **kwargs):
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(agents_module, "record_forecast", broken_ledger)
    report = run_agent_pipeline(
        "BTCUSDT", complete_agents(), as_of="2026-01-01T00:00:00Z"
    )

    prediction_stage = next(stage for stage in report.stages if stage.stage == "prediction")
    assert "not frozen in the ledger" in prediction_stage.result["ledger_error"]
    assert any("not frozen in the ledger" in warning for warning in report.warnings)
    # A bookkeeping failure must not silently upgrade a forecast into a trade.
    assert report.action == "BUY"


def test_pipeline_forces_no_trade_when_probability_is_not_calibrated():
    report = run_agent_pipeline("ETHUSDT", complete_agents(calibrated=False))
    assert report.status == "completed_no_trade"
    assert report.action == "NO TRADE"
    assert any("not demonstrably calibrated" in warning for warning in report.warnings)


def test_pipeline_forces_no_trade_for_invalid_probability():
    report = run_agent_pipeline("ETHUSDT", complete_agents(probability=1.5))
    assert report.action == "NO TRADE"
    assert report.status == "completed_no_trade"


def test_pipeline_records_missing_agent_and_continues_remaining_stages():
    agents = complete_agents()
    del agents["research"]
    report = run_agent_pipeline("SOLUSDT", agents)
    assert report.status == "incomplete"
    assert report.action == "NO TRADE"
    assert [stage.stage for stage in report.stages] == list(PIPELINE_STAGES)
    assert report.stages[2].status == "not_configured"
    assert report.stages[3].status == "completed"
    assert report.stages[4].status == "completed"
    assert report.stages[5].status == "completed"
    assert report.stages[6].status == "completed"


def test_pipeline_records_agent_exception_and_continues_remaining_stages():
    agents = complete_agents()
    agents["market_analysis"] = lambda context: (_ for _ in ()).throw(RuntimeError("fixture failure"))
    report = run_agent_pipeline("SOLUSDT", agents)
    assert report.status == "failed"
    assert report.action == "NO TRADE"
    assert [stage.stage for stage in report.stages] == list(PIPELINE_STAGES)
    assert report.stages[1].stage == "market_analysis"
    assert report.stages[1].status == "failed"
    assert report.stages[-1].stage == "report"
    assert report.stages[-1].status == "completed"


def test_pipeline_marks_expected_research_unavailability_without_calling_it_a_failure():
    from app.research_agent import ResearchUnavailable

    agents = complete_agents()
    agents["report"] = run_report_agent
    agents["research"] = lambda context: (_ for _ in ()).throw(
        ResearchUnavailable("No research evidence available for this run")
    )
    report = run_agent_pipeline("BTCUSDT", agents)
    research_stage = next(stage for stage in report.stages if stage.stage == "research")
    assert research_stage.status == "unavailable"
    assert any("No research evidence" in error for error in research_stage.errors)
    assert report.status == "incomplete"
    assert report.action == "NO TRADE"
    assert report.stages[-1].stage == "report"
    assert report.stages[-1].status == "completed"
    assert report.stages[-1].result["decision"] == "NO TRADE"


def test_pipeline_requires_explicit_risk_authorization_for_non_no_trade_actions():
    agents = complete_agents()
    agents["risk_decision"] = lambda context: {"action": "BUY", "authorized": False}
    report = run_agent_pipeline("BTCUSDT", agents)
    assert report.status == "completed_no_trade"
    assert report.action == "NO TRADE"
    assert any("did not explicitly authorize" in warning for warning in report.warnings)


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

