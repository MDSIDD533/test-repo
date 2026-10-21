from __future__ import annotations
from datetime import datetime, timezone
from threading import RLock
import os

STARTING_BALANCE = float(os.getenv('PAPER_STARTING_BALANCE', '1000'))
RISK_PERCENT = float(os.getenv('PAPER_RISK_PERCENT', '1'))
LIVE_TRADING_ENABLED = False
_lock = RLock()
_equity = STARTING_BALANCE
_trades: list[dict] = []

def account() -> dict:
    with _lock:
        return {'starting_balance':STARTING_BALANCE,'equity':round(_equity,2),'risk_percent':RISK_PERCENT,'open_trades':sum(t['status']=='OPEN' for t in _trades),'closed_trades':sum(t['status']=='CLOSED' for t in _trades),'mode':'PAPER ONLY','live_trading_enabled':False}

def open_trade(signal: dict) -> dict:
    if signal.get('signal') != 'BUY': raise ValueError('Paper entries require a BUY signal; SELL means exit/avoid, not short.')
    entry, stop = float(signal['entry']), float(signal['sl'])
    risk_per_unit = entry - stop
    if entry <= 0 or risk_per_unit <= 0: raise ValueError('Invalid entry or stop-loss')
    with _lock:
        risk_budget = _equity * RISK_PERCENT / 100
        qty = risk_budget / risk_per_unit
        trade = {'id':len(_trades)+1,'symbol':signal['symbol'],'entry':entry,'quantity':round(qty,8),'sl':stop,'tp1':signal['tp1'],'tp2':signal['tp2'],'tp3':signal['tp3'],'status':'OPEN','opened_at':datetime.now(timezone.utc).isoformat(),'mode':'PAPER'}
        _trades.append(trade)
        return trade.copy()

def list_trades() -> list[dict]:
    with _lock: return [t.copy() for t in reversed(_trades)]

def update_prices(prices: dict[str, float]) -> int:
    """Close paper BUY trades when the latest market price reaches SL or TP1."""
    closed = 0
    with _lock:
        global _equity
        for trade in _trades:
            if trade['status'] != 'OPEN':
                continue
            price = prices.get(trade['symbol'])
            if price is None or price <= 0:
                continue
            exit_reason = None
            if price <= float(trade['sl']):
                exit_reason = 'STOP_LOSS'
            elif price >= float(trade['tp1']):
                exit_reason = 'TP1'
            if exit_reason is None:
                continue
            exit_price = float(price)
            pnl = (exit_price - float(trade['entry'])) * float(trade['quantity'])
            _equity += pnl
            trade.update({
                'status': 'CLOSED',
                'exit': round(exit_price, 8),
                'pnl': round(pnl, 2),
                'exit_reason': exit_reason,
                'closed_at': datetime.now(timezone.utc).isoformat(),
            })
            closed += 1
    return closed

