from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHART_PAGE = ROOT / "static" / "chart.html"
DASHBOARD_PAGE = ROOT / "static" / "index.html"
CHART_CORE = ROOT / "static" / "chart-core.js"


class LiveChartPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = CHART_PAGE.read_text(encoding="utf-8")
        cls.dashboard = DASHBOARD_PAGE.read_text(encoding="utf-8")
        cls.core = CHART_CORE.read_text(encoding="utf-8")

    def test_chart_loads_testable_core_module(self):
        self.assertIn('<script src="/chart-core.js"></script>', self.chart)
        self.assertIn("const Core = window.ChartCore;", self.chart)
        self.assertIn("new Core.LiveStream(", self.chart)

    def test_chart_refreshes_automatically_every_15_seconds(self):
        self.assertIn("const AUTO_REFRESH_MS = 15000;", self.chart)
        self.assertRegex(
            self.chart,
            re.compile(r"setInterval\(\(\) => \{\s*if \(!document\.hidden\) load\(\{ auto: true \}\);\s*\}, AUTO_REFRESH_MS\);"),
        )

    def test_chart_refreshes_when_tab_becomes_visible(self):
        self.assertRegex(
            self.chart,
            re.compile(r"document\.addEventListener\('visibilitychange', \(\) => \{\s*if \(!document\.hidden\) load\(\{ auto: true \}\);"),
        )

    def test_rest_refresh_preserves_zoom_and_only_new_selection_fits_content(self):
        self.assertIn("if (state.renderedKey !== key || !state.hasFit) {", self.chart)
        self.assertIn("state.chart.timeScale().fitContent();", self.chart)
        self.assertNotIn("if(!autoRefresh)chart.timeScale().fitContent()", self.chart)

    def test_overlapping_requests_are_preempted_or_skipped(self):
        self.assertIn("if (auto && state.inflight) return;", self.chart)
        self.assertIn("if (state.inflight) state.inflight.abort();", self.chart)
        self.assertIn("const controller = new AbortController();", self.chart)
        self.assertIn("if (seq !== state.loadSeq) return;", self.chart)

    def test_every_supported_timeframe_is_selectable(self):
        for interval in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"):
            self.assertRegex(
                self.chart,
                re.compile(r'<option value="' + re.escape(interval) + r'"'),
                msg=f"missing timeframe option {interval}",
            )

    def test_selected_timeframe_is_sent_to_candle_and_analysis_apis(self):
        self.assertIn("'/api/candle-signals/' + pair + '?interval=' + interval + '&limit=' + limit", self.chart)
        self.assertIn("'/api/analyze/' + pair + '?interval=' + interval", self.chart)
        self.assertIn("if (candlePayload.interval !== interval)", self.chart)
        self.assertIn("if (analysis.timeframe !== interval)", self.chart)

    def test_symbol_and_timeframe_changes_reload_and_stop_the_old_stream(self):
        self.assertIn("$('interval').onchange = () => load({ auto: false });", self.chart)
        self.assertIn("$('symbol').addEventListener('change', () => load({ auto: false }));", self.chart)
        self.assertIn("if (key !== state.renderedKey) {\n      stopStream();", self.chart)
        self.assertIn("function stopStream() {\n    if (state.stream) state.stream.stop();", self.chart)

    def test_live_badge_is_driven_by_stream_state_not_rest_success(self):
        self.assertIn("live: 'LIVE'", self.chart)
        self.assertIn("rest: 'REST ONLY'", self.chart)
        self.assertIn("stale: 'STALE'", self.chart)
        self.assertIn("offline: 'OFFLINE'", self.chart)
        self.assertIn("setConnection('rest',", self.chart)
        self.assertNotIn("setStatus('LIVE", self.chart)

    def test_automatic_refresh_does_not_restart_an_offline_stream(self):
        self.assertIn("function ensureStream(symbol, interval, forceRestart)", self.chart)
        self.assertIn("snapshot.state === 'offline'", self.chart)
        self.assertIn("ensureStream(symbol, interval, !auto);", self.chart)

    def test_stale_feed_is_detected_and_reported(self):
        self.assertIn("const STALE_AFTER_MS = 30000;", self.chart)
        self.assertIn("state.stream.isStaleAt(Date.now(), STALE_AFTER_MS)", self.chart)
        self.assertIn("No live message for more than", self.chart)

    def test_forming_candle_is_distinguished_from_completed_candle(self):
        self.assertIn("FORMING CANDLE", self.chart)
        self.assertIn("LAST CANDLE CLOSED", self.chart)
        self.assertIn("updateCandleState(!candle.closed);", self.chart)
        self.assertIn("updateCandleState(built.forming);", self.chart)
        self.assertIn("const forming = last.time + size > nowSeconds;", self.core)

    def test_chart_displays_symbol_timeframe_price_and_last_update(self):
        for element_id in (
            "metaSymbol", "metaInterval", "currentPrice", "candleCount",
            "lastUpdate", "chartTitle", "dataSource",
        ):
            self.assertIn(f'id="{element_id}"', self.chart)
        self.assertIn("setLastUpdate(new Date());", self.chart)
        self.assertIn("' · ' + money(price)", self.chart)

    def test_dashboard_loads_chart_without_lazy_loading_delay(self):
        self.assertRegex(
            self.dashboard,
            re.compile(r'<iframe[^>]*id="chartFrame"[^>]*loading="eager"', re.DOTALL),
        )
        self.assertIn('src="/chart?embed=1"', self.dashboard)

    def test_manual_refresh_button_is_available(self):
        self.assertIn('id="load"', self.chart)
        self.assertIn("$('load').onclick = () => load({ auto: false });", self.chart)


if __name__ == "__main__":
    unittest.main()
