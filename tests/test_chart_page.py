from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHART_PAGE = ROOT / "static" / "chart.html"
DASHBOARD_PAGE = ROOT / "static" / "index.html"


class LiveChartPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = CHART_PAGE.read_text(encoding="utf-8")
        cls.dashboard = DASHBOARD_PAGE.read_text(encoding="utf-8")

    def test_chart_refreshes_automatically_every_15_seconds(self):
        self.assertIn("setInterval(()=>{if(!document.hidden)load(true)},15000)", self.chart)

    def test_chart_refreshes_when_tab_becomes_visible(self):
        self.assertIn("document.addEventListener('visibilitychange',()=>{if(!document.hidden)load(true)})", self.chart)

    def test_background_refresh_does_not_reset_zoom(self):
        self.assertIn("if(!autoRefresh)chart.timeScale().fitContent()", self.chart)

    def test_refresh_requests_do_not_overlap(self):
        self.assertIn("if(chartLoading)return;chartLoading=true", self.chart)
        self.assertIn("finally{chartLoading=false;}", self.chart)

    def test_chart_reports_last_successful_refresh_time(self):
        self.assertIn("last updated ", self.chart)
        self.assertIn("Market data unavailable: '+e.message", self.chart)

    def test_dashboard_loads_chart_without_lazy_loading_delay(self):
        self.assertRegex(
            self.dashboard,
            re.compile(r'<iframe[^>]*id="chartFrame"[^>]*loading="eager"', re.DOTALL),
        )

    def test_manual_refresh_button_is_available(self):
        self.assertIn('id="load">Refresh now</button>', self.chart)

    def test_chart_opens_live_binance_kline_stream_for_selected_symbol_and_interval(self):
        self.assertIn("new WebSocket('wss://stream.binance.com:9443/ws/'+symbol.toLowerCase()+'@kline_'+interval)", self.chart)
        self.assertIn('candleSeries.update(next)', self.chart)
        self.assertIn('connectLiveStream(symbol,interval)', self.chart)

    def test_selected_timeframe_is_sent_to_candle_and_analysis_apis(self):
        self.assertIn("api('/api/candle-signals/'+pair+'?interval='+interval+'&limit='+limit)", self.chart)
        self.assertIn("api('/api/analyze/'+pair+'?interval='+interval)", self.chart)
        self.assertIn("$('chartTitle').textContent=symbol+' · '+interval", self.chart)


if __name__ == "__main__":
    unittest.main()
