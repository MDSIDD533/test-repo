import json

from scripts.summarize_forecast_batch import summarize_reports


def test_summary_separates_comparisons_from_data_failures(tmp_path):
    (tmp_path / "trial-001.json").write_text(json.dumps({
        "market_data_provider": "coinbase:BTC-USD",
        "comparison": {
            "direction_match": True,
            "almost_same": True,
            "absolute_price_error_pct_of_start": 0.02,
        },
    }), encoding="utf-8")
    (tmp_path / "trial-001.exitcode").write_text("0", encoding="utf-8")
    (tmp_path / "trial-002.json").write_text(json.dumps({
        "error": "RuntimeError: public data API unavailable",
    }), encoding="utf-8")
    (tmp_path / "trial-002.exitcode").write_text("1", encoding="utf-8")

    result = summarize_reports(tmp_path)

    assert result["report_json_files_found"] == 2
    assert result["completed_market_comparisons"] == 1
    assert result["direction_accuracy_pct"] == 100.0
    assert result["almost_same_passes"] == 1
    assert result["exit_code_zero_count"] == 1
    assert result["exit_code_nonzero_count"] == 1
    assert result["failed_or_incomplete_trials"] == ["trial-002.json"]


def test_summary_handles_empty_directory(tmp_path):
    result = summarize_reports(tmp_path)
    assert result["completed_market_comparisons"] == 0
    assert result["direction_accuracy_pct"] is None
    assert result["almost_same_pass_rate_pct"] is None
