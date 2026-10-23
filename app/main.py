import json
from datetime import datetime, timezone
from pathlib import Path
import time
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from app.stream_relay import (
    INTERVAL_SECONDS as STREAM_INTERVALS, provider_order, stream_messages,
)
from app.market import (
    MarketDataError, REPLAY_MODE, all_24h_tickers, candles, provider_used, replay_metadata,
)
from app.analysis import analyze_candles
from app import paper
from app.agents import run_agent_pipeline
from app.market_data_agent import make_market_data_agent
from app.market_analysis_agent import run_market_analysis_agent
from app.timetraveler_agent import run_timetraveler_agent
from app.research_agent import make_research_agent
from app.news_provider import fetch_crypto_news
from app.prediction_agent import run_prediction_agent
from app.risk_decision_agent import run_risk_decision_agent
from app.report_agent import run_report_agent
from app.short_horizon_forecast import make_two_minute_forecast
from app.forecast_ledger import LedgerError, read_ledger, record_resolution
from app import observability

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title='Crypto Spot Trading Analysis Agent', version='0.1.0', description='Analysis and paper trading only. Live trading is disabled.')
class PaperOpenRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=30)


class ForecastResolutionRequest(BaseModel):
    observed_price: float = Field(gt=0)
    observed_at: str | None = None
    source: str = Field(default='manual_observation', min_length=1, max_length=80)
    time_difference_seconds: float | None = None

@app.middleware('http')
async def record_request(request: Request, call_next):
    """Log every request with its outcome. A detail string is logged for failures."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code >= 500:
        level = 'error'
    elif response.status_code >= 400:
        level = 'warning'
    else:
        level = 'info'
    observability.log_event(
        level,
        'http_request',
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(elapsed, 2),
    )
    return response


@app.get('/api/logs/recent')
def recent_logs(limit: int = Query(default=50, ge=1, le=500), level: str | None = None):
    """Operator-visible tail of structured events, newest first."""
    try:
        return {
            'entries': observability.recent(limit, level=level),
            'summary': observability.summary(),
        }
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'Event log is unreadable: {exc}')


@app.get('/', response_class=HTMLResponse)
def dashboard(): return (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')

@app.get('/chart', response_class=HTMLResponse)
def full_chart(): return (ROOT / 'static' / 'chart.html').read_text(encoding='utf-8')

@app.get('/chart-core.js', include_in_schema=False)
def chart_core_js():
    return FileResponse(ROOT / 'static' / 'chart-core.js', media_type='application/javascript')

@app.get('/api/reports/forecast-evaluation')
def forecast_evaluation_report():
    path = ROOT / 'reports' / 'forecast-model-evaluation-2026-10-21.json'
    if not path.exists():
        raise HTTPException(status_code=404, detail='Recorded forecast evaluation report is unavailable')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f'Recorded forecast evaluation report is unreadable: {exc}')

@app.get('/api/reports/batch-summary')
def batch_summary_report():
    """Serve the committed summary of the last live batch, or say it is missing."""
    summary_path = ROOT / 'docs' / 'evidence' / 'batch-150-summary.json'
    evaluation_path = ROOT / 'docs' / 'evidence' / 'batch-150-evaluation.json'
    if not summary_path.exists():
        raise HTTPException(status_code=404, detail='No recorded live batch summary is available')
    try:
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        evaluation = (
            json.loads(evaluation_path.read_text(encoding='utf-8'))
            if evaluation_path.exists() else None
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f'Recorded batch summary is unreadable: {exc}')
    return {
        'run_id': '39761203609',
        'commit': '806dff1',
        'recorded_on': '2026-10-22',
        'trials_requested': 150,
        'summary': summary,
        'evaluation': evaluation,
        'is_live_now': False,
        'note': 'Stored result of a completed live batch, not a live measurement.',
    }


@app.get('/api/stream/providers')
def stream_providers(symbol: str = 'BTCUSDT'):
    """List the stream providers this server would try, in order.

    Exposed so the UI and the browser tests can prove which upstream is in use
    instead of assuming Binance.
    """
    import os as _os

    override = _os.getenv('STREAM_PROVIDER', 'auto').strip().lower()
    return {
        'symbol': symbol.strip().upper(),
        'candidates': provider_order(symbol.strip().upper(), override),
        'override': override,
        'mode': 'replay' if REPLAY_MODE else 'live',
        'note': (
            'Replay mode serves a recording over REST; no live stream is opened.'
            if REPLAY_MODE else
            'The relay tries these in order and every candle states which one produced it.'
        ),
    }


@app.websocket('/ws/candles')
async def candles_stream(websocket: WebSocket, symbol: str = 'BTCUSDT', interval: str = '5m'):
    """Relay a live candle stream from the app's own origin.

    The browser no longer connects to Binance directly: Binance geo-blocks many
    networks (HTTP 451), and a third-party socket cannot be verified from the
    page. The relay tries Binance, then Coinbase, and every message states which
    provider produced it.
    """
    await websocket.accept()
    symbol = (symbol or 'BTCUSDT').strip().upper()
    interval = (interval or '5m').strip()
    if interval not in STREAM_INTERVALS:
        await websocket.close(code=1008, reason=f'Unsupported interval: {interval}')
        return
    if REPLAY_MODE:
        # A recording does not update. Refusing here is what keeps replay honest.
        await websocket.close(code=1008, reason='Replay mode serves a recording; no live stream')
        return

    errors: list[dict] = []
    sent = 0
    try:
        async for message in stream_messages(symbol, interval, errors=errors):
            await websocket.send_text(json.dumps(message))
            sent += 1
        if sent == 0:
            # No provider worked. Leaving the socket open would look like a
            # healthy but silent feed, so close it and say why.
            observability.log_event(
                'warning', 'stream_unavailable',
                symbol=symbol, interval=interval, provider_errors=errors,
            )
            await websocket.close(code=1011, reason='no stream provider was reachable')
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        observability.log_event('warning', 'stream_error', symbol=symbol, interval=interval,
                     error=f'{type(exc).__name__}: {exc}', provider_errors=errors)
        try:
            await websocket.close(code=1011, reason='stream failed')
        except RuntimeError:
            pass


@app.get('/api/health')
def health():
    data = replay_metadata()
    return {
        'status': 'ok',
        'version': app.version,
        'mode': 'PAPER ONLY',
        'live_trading_enabled': False,
        'data_mode': data.get('mode', 'live'),
        'replay': data if data.get('mode') == 'replay' else None,
    }


@app.get('/api/data-mode')
def data_mode():
    """State whether candles are live or replayed, and what the recording is."""
    return replay_metadata()


@app.get('/api/agents/run/{symbol}')
def run_agents(symbol: str, interval: str = '5m', simulation_count: int = Query(default=100)):
    """Run implemented pipeline stages; unavailable stages keep the result NO TRADE."""
    if simulation_count not in {100, 500, 1000}:
        raise HTTPException(status_code=400, detail='simulation_count must be 100, 500, or 1000')
    try:
        market_agent = make_market_data_agent(
            candles, interval=interval, limit=220, replay=REPLAY_MODE
        )
        research_agent = make_research_agent(fetch_crypto_news)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    report = run_agent_pipeline(
        symbol,
        {
            'market_data': market_agent,
            'market_analysis': run_market_analysis_agent,
            'timetraveler': run_timetraveler_agent,
            'research': research_agent,
            'prediction': run_prediction_agent,
            'risk_decision': run_risk_decision_agent,
            'report': run_report_agent,
        },
        simulation_count=simulation_count,
    ).to_dict()
    stages = {stage['stage']: stage['status'] for stage in report.get('stages', [])}
    failed = [name for name, status in stages.items() if status in {'failed', 'unavailable', 'not_configured'}]
    observability.log_event(
        'warning' if failed else 'info',
        'agent_pipeline',
        run_id=report.get('run_id'),
        symbol=report.get('symbol'),
        status=report.get('status'),
        action=report.get('action'),
        stages=stages,
        degraded_stages=failed,
    )
    return report

@app.get('/api/forecast/two-minute/{symbol}')
def two_minute_forecast_preview(symbol: str):
    """Return an unrecorded 120-second forecast preview from completed 1m candles.

    This endpoint is deliberately labeled as a preview. It is not a frozen
    experiment record and cannot authorize a trade.
    """
    try:
        rows = candles(symbol, '1m', 40)
        now = time.time()
        closed = [row for row in rows if float(row['time']) + 60 <= now]
        if len(closed) < 35:
            raise ValueError('Not enough completed 1-minute candles for a two-minute forecast')
        analysis = analyze_candles(closed, symbol, '1m')
        created_at = datetime.now(timezone.utc).isoformat()
        research_items = []
        research_status = 'unavailable'
        research_error = None
        try:
            research_items = fetch_crypto_news(symbol, created_at)
            research_status = 'available' if research_items else 'no_matching_recent_headlines'
        except Exception as exc:
            research_error = f'{type(exc).__name__}: {exc}'
        forecast = make_two_minute_forecast(
            closed, symbol, analysis.get('signal', 'WAIT'), research_items
        )
        forecast.update({
            'forecast_created_at': created_at,
            'baseline_source': 'latest completed 1m candle close (REST preview)',
            'research_status': research_status,
            'research_error': research_error,
            'technical_signal': analysis.get('signal'),
            'technical_reasons': analysis.get('reasons', []),
            'recorded': False,
            'trade_authorized': False,
            'observation_note': (
                'Preview only. For a frozen trial with an exact spot-ticker horizon, run '
                'scripts/two_minute_forecast_test.py; this endpoint does not persist a forecast.'
            ),
        })
        return forecast
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@app.get('/api/forecast/ledger')
def forecast_ledger(limit: int = Query(default=50, ge=1, le=500)):
    """Return frozen forecasts and their resolutions.

    The ledger is append-only on disk. This endpoint only folds events; it
    never edits or deletes a forecast.
    """
    try:
        data = read_ledger()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'Forecast ledger is unreadable: {exc}')
    data['storage'] = 'append-only JSONL'
    data['forecasts'] = data['forecasts'][:limit]
    data['returned_forecasts'] = len(data['forecasts'])
    return data


@app.post('/api/forecast/ledger/{forecast_id}/resolve')
def resolve_forecast(forecast_id: str, request: ForecastResolutionRequest):
    """Append an observed outcome for a frozen forecast.

    The caller supplies the observed price and its provenance. Nothing is
    fetched here, so an outcome can never be silently back-filled with a price
    from a different source than the one reported.
    """
    try:
        return record_resolution(
            forecast_id,
            observed_price=request.observed_price,
            observed_at=request.observed_at,
            source=request.source,
            time_difference_seconds=request.time_difference_seconds,
        )
    except (LedgerError, ValueError) as exc:
        # LedgerError subclasses ValueError. Matching on the base type keeps
        # this correct even if the module is reloaded and a fresh exception
        # class object exists.
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'Forecast ledger write failed: {exc}')


@app.get('/api/market/summary')
def market_summary():
    try:
        rows=all_24h_tickers(); return {'pair_count':len(rows),'top_pairs':rows[:10]}
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))

@app.get('/api/scanner')
def scanner(limit:int=Query(default=2000,ge=10,le=3000),analyze_top:int=Query(default=20,ge=0,le=50)):
    if REPLAY_MODE:
        from app.market import replay_tickers
        try:
            rows = replay_tickers()
        except MarketDataError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        output = []
        for i, row in enumerate(rows):
            item = {**row, 'signal': 'NOT SCANNED', 'confidence': None, 'reasons': []}
            if i < analyze_top:
                try:
                    result = analyze_candles(candles(row['symbol'], '5m', 220), row['symbol'], '5m')
                    item.update(signal=result['signal'], confidence=result['confidence'], reasons=result['reasons'][:2])
                except (MarketDataError, ValueError):
                    item['signal'] = 'DATA ERROR'
            output.append(item)
        metadata = replay_metadata()
        return {
            'total_pairs': len(rows),
            'analyzed_pairs': min(analyze_top, len(rows)),
            'returned': output[:limit],
            'timeframe': '5m',
            'data_mode': 'replay',
            'recording': metadata,
            'note': (
                'REPLAY: the scanner is limited to the symbol in the recording. '
                'No live pair discovery is performed.'
            ),
        }
    try:
        rows=all_24h_tickers(); output=[]
        for i,row in enumerate(rows):
            item={**row,'signal':'NOT SCANNED','confidence':None,'reasons':[]}
            if i < analyze_top:
                try:
                    result=analyze_candles(candles(row['symbol'],'5m',220),row['symbol'],'5m')
                    item.update(signal=result['signal'],confidence=result['confidence'],reasons=result['reasons'][:2])
                except (MarketDataError,ValueError): item['signal']='DATA ERROR'
            output.append(item)
        return {'total_pairs':len(rows),'analyzed_pairs':min(analyze_top,len(rows)),'returned':output[:limit],'timeframe':'5m','note':'All eligible pairs are discovered; detailed candle analysis is limited to top-volume pairs to respect public API rate limits.'}
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))

@app.get('/api/candles/{symbol}')
def get_candles(symbol:str,interval:str='5m',limit:int=Query(default=200,ge=35,le=500)):
    try:
        rows = candles(symbol, interval, limit)
        return {
            'symbol': symbol.upper(),
            'interval': interval,
            'candles': rows,
            # State the actual source rather than always claiming Binance.
            'provider': provider_used(symbol, interval),
        }
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.get('/api/candle-signals/{symbol}')
def candle_signals(symbol:str,interval:str='5m',limit:int=Query(default=200,ge=35,le=500)):
    """Return historical candles colored by the same rule-based signal engine."""
    try:
        cs=candles(symbol,interval,limit)
        output=[]
        for index,candle in enumerate(cs):
            signal='WAIT'
            if index >= 34:
                try:
                    signal=analyze_candles(cs[:index+1],symbol,interval)['signal']
                except ValueError:
                    signal='WAIT'
            output.append({**candle,'signal':signal})
        return {
            'symbol': symbol.upper(),
            'interval': interval,
            'candles': output,
            'signal_colors': {'BUY': 'yellow', 'SELL': 'blue', 'WAIT': 'normal'},
            'provider': provider_used(symbol, interval),
        }
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.get('/api/analyze/{symbol}')
def analyze(symbol:str,interval:str='5m'):
    try:
        result=analyze_candles(candles(symbol,interval,220),symbol,interval); confirmations={}
        for tf in ('15m','1h'):
            try: confirmations[tf]=analyze_candles(candles(symbol,tf,220),symbol,tf)['signal']
            except (MarketDataError,ValueError): confirmations[tf]='UNAVAILABLE'
        result['multi_timeframe']=confirmations; result['confirmed']=all(v==result['signal'] for v in confirmations.values()); return result
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.get('/api/paper/account')
def paper_account(): return paper.account()
@app.get('/api/paper/trades')
def paper_trades(): return {'trades':paper.list_trades()}

@app.post('/api/paper/refresh')
def paper_refresh():
    try:
        if REPLAY_MODE:
            from app.market import replay_tickers
            rows = replay_tickers()
        else:
            rows = all_24h_tickers()
        prices = {row['symbol']: row['price'] for row in rows}
        closed = paper.update_prices(prices)
        return {'updated': True, 'closed_trades': closed, 'account': paper.account()}
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
@app.post('/api/paper/open')
def paper_open(request:PaperOpenRequest):
    try: return paper.open_trade(analyze(request.symbol,'5m'))
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.post('/api/backtest/{symbol}')
def backtest(symbol:str,interval:str='5m',limit:int=Query(default=500,ge=100,le=500),fee_bps:float=Query(default=10.0,ge=0,le=100),slippage_bps:float=Query(default=0.0,ge=0,le=100)):
    try:
        cs=candles(symbol,interval,limit)
        fee_rate=fee_bps/10000.0
        slippage_rate=slippage_bps/10000.0
        balance=1000.0
        wins=losses=0
        trades=[]
        next_available_index=80
        for end in range(80,len(cs)-1):
            # Do not open another position while the previous simulated trade is active.
            if end < next_available_index:
                continue
            result=analyze_candles(cs[:end],symbol,interval)
            if result['signal']!='BUY':
                continue
            # The signal is calculated using candles strictly before the entry candle.
            signal_entry=float(result['entry'])
            entry=float(cs[end]['open'])*(1+slippage_rate)
            risk=max(signal_entry-float(result['sl']),0.0)
            reward=max(float(result['tp1'])-signal_entry,0.0)
            if entry<=0 or risk<=0 or reward<=0:
                continue
            # Preserve the signal's risk/reward distances after the next-candle opening gap.
            stop=entry-risk
            target=entry+reward
            future=cs[end:min(end+25,len(cs))]
            exit_price=None
            exit_index=None
            outcome='TIME_EXIT'
            for offset,candle in enumerate(future):
                candle_index=end+offset
                candle_open=float(candle['open'])
                # Model gap fills at the open; when both levels are touched intrabar,
                # conservatively count the stop first.
                if candle_open<=stop:
                    exit_price=candle_open*(1-slippage_rate); outcome='LOSS'; exit_index=candle_index; break
                if candle_open>=target:
                    exit_price=candle_open*(1-slippage_rate); outcome='WIN'; exit_index=candle_index; break
                if candle['low']<=stop:
                    exit_price=stop*(1-slippage_rate); outcome='LOSS'; exit_index=candle_index; break
                if candle['high']>=target:
                    exit_price=target*(1-slippage_rate); outcome='WIN'; exit_index=candle_index; break
            if exit_price is None:
                exit_index=end+len(future)-1 if future else end
                exit_price=(future[-1]['close'] if future else entry)*(1-slippage_rate)
            next_available_index=exit_index+1
            gross_pct=(exit_price-entry)/entry if entry else 0
            entry_cost=entry*(1+fee_rate)
            exit_proceeds=exit_price*(1-fee_rate)
            net_pct=(exit_proceeds-entry_cost)/entry_cost if entry_cost else 0
            balance*=1+net_pct
            wins+=outcome=='WIN'
            losses+=outcome=='LOSS'
            trades.append({
                'entry':entry,
                'exit':exit_price,
                'outcome':outcome,
                'gross_return_pct':round(gross_pct*100,4),
                'return_pct':round(net_pct*100,4),
                'fees_and_costs_pct':round((gross_pct-net_pct)*100,4),
                'entry_candle_index':end,
                'exit_candle_index':exit_index,
            })
        return {
            'symbol':symbol.upper(),
            'interval':interval,
            'initial_balance':1000,
            'final_balance':round(balance,2),
            'trades':len(trades),
            'wins':wins,
            'losses':losses,
            'win_rate_pct':round(wins/len(trades)*100,2) if trades else 0,
            'fee_bps_per_side':fee_bps,
            'slippage_bps_per_side':slippage_bps,
            'results':trades,
            'warning':'Simplified backtest includes configurable fees and slippage but excludes spread, order-book depth and intrabar tick data; it allows only one open position at a time and does not predict future performance.',
        }
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))
