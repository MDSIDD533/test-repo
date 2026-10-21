from pathlib import Path
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from app.market import MarketDataError, all_24h_tickers, candles
from app.analysis import analyze_candles
from app import paper

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title='Crypto Spot Trading Analysis Agent', version='0.1.0', description='Analysis and paper trading only. Live trading is disabled.')
class PaperOpenRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=30)

@app.get('/', response_class=HTMLResponse)
def dashboard(): return (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')

@app.get('/api/health')
def health(): return {'status':'ok','version':app.version,'mode':'PAPER ONLY','live_trading_enabled':False}

@app.get('/api/market/summary')
def market_summary():
    try:
        rows=all_24h_tickers(); return {'pair_count':len(rows),'top_pairs':rows[:10]}
    except MarketDataError as exc: raise HTTPException(status_code=502,detail=str(exc))

@app.get('/api/scanner')
def scanner(limit:int=Query(default=2000,ge=10,le=3000),analyze_top:int=Query(default=20,ge=0,le=50)):
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
    try: return {'symbol':symbol.upper(),'interval':interval,'candles':candles(symbol,interval,limit)}
    except (MarketDataError,ValueError) as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.get('/api/analyze/{symbol}')
def analyze(symbol:str,interval:str='5m'):
    try:
        result=analyze_candles(candles(symbol,interval,220),symbol,interval); confirmations={}
        for tf in ('15m','1h'):
            try: confirmations[tf]=analyze_candles(candles(symbol,tf,220),symbol,tf)['signal']
            except (MarketDataError,ValueError): confirmations[tf]='UNAVAILABLE'
        result['multi_timeframe']=confirmations; result['confirmed']=all(v==result['signal'] for v in confirmations.values()); return result
    except (MarketDataError,ValueError) as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.get('/api/paper/account')
def paper_account(): return paper.account()
@app.get('/api/paper/trades')
def paper_trades(): return {'trades':paper.list_trades()}
@app.post('/api/paper/open')
def paper_open(request:PaperOpenRequest):
    try: return paper.open_trade(analyze(request.symbol,'5m'))
    except ValueError as exc: raise HTTPException(status_code=400,detail=str(exc))

@app.post('/api/backtest/{symbol}')
def backtest(symbol:str,interval:str='5m',limit:int=Query(default=500,ge=100,le=500)):
    try:
        cs=candles(symbol,interval,limit); balance=1000.0; wins=0; losses=0; trades=[]
        for end in range(80,len(cs)-1):
            result=analyze_candles(cs[:end],symbol,interval)
            if result['signal']!='BUY': continue
            entry=cs[end]['open']; stop=result['sl']; target=result['tp1']; future=cs[end+1:min(end+25,len(cs))]; exit_price=None; outcome='TIME_EXIT'
            for candle in future:
                if candle['low']<=stop: exit_price=stop; outcome='LOSS'; break
                if candle['high']>=target: exit_price=target; outcome='WIN'; break
            if exit_price is None: exit_price=future[-1]['close'] if future else entry
            pct=(exit_price-entry)/entry if entry else 0; balance*=1+pct; wins+=outcome=='WIN'; losses+=outcome=='LOSS'
            trades.append({'entry':entry,'exit':exit_price,'outcome':outcome,'return_pct':round(pct*100,3)})
        return {'symbol':symbol.upper(),'interval':interval,'initial_balance':1000,'final_balance':round(balance,2),'trades':len(trades),'wins':wins,'losses':losses,'win_rate_pct':round(wins/len(trades)*100,2) if trades else 0,'results':trades,'warning':'Simplified backtest excludes fees and slippage; not a prediction of future performance.'}
    except (MarketDataError,ValueError) as exc: raise HTTPException(status_code=400,detail=str(exc))
