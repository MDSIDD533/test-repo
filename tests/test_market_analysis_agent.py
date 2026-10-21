import pytest

from app.market_analysis_agent import run_market_analysis_agent


def rising_candles(count=220):
    rows = []
    price = 100.0
    for index in range(count):
        price += 0.15
        rows.append({
            "time": 1_700_000_000.0 + index * 300,
            "open": price - 0.04,
            "high": price + 0.2,
            "low": price - 0.2,
            "close": price,
            "volume": 10.0,
        })
    return rows


def valid_context():
    candles = rising_candles()
    return {
        "symbol": "BTCUSDT",
        "market_data": {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "candles": candles,
            "validation": {
                "valid": True,
                "candle_count": len(candles),
                "latest_candle_time": candles[-1]["time"],
            },
        },
    }


def test_market_analysis_produces_technical_snapshot():
    result = run_market_analysis_agent(valid_context())
    assert result["symbol"] == "BTCUSDT"
    assert result["interval"] == "5m"
    assert result["signal"] == "BUY"
    assert result["sl"] < result["entry"] < result["tp1"] < result["tp2"] < result["tp3"]
    assert result["evidence"][0]["candle_count"] == 220
    assert "not a calibrated probability" in result["warnings"][0]


def test_analysis_refuses_missing_market_data():
    with pytest.raises(ValueError, match="market_data stage"):
        run_market_analysis_agent({"symbol": "BTCUSDT"})


def test_analysis_refuses_unvalidated_market_data():
    context = valid_context()
    context["market_data"]["validation"]["valid"] = False
    with pytest.raises(ValueError, match="pass validation"):
        run_market_analysis_agent(context)


def test_analysis_refuses_missing_symbol_or_interval():
    context = valid_context()
    context["market_data"]["symbol"] = ""
    with pytest.raises(ValueError, match="symbol and interval"):
        run_market_analysis_agent(context)
