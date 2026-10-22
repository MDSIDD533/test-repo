from __future__ import annotations

from datetime import datetime, timezone
import os
from threading import RLock

STARTING_BALANCE = float(os.getenv('PAPER_STARTING_BALANCE', '1000'))
RISK_PERCENT = float(os.getenv('PAPER_RISK_PERCENT', '1'))
MAX_OPEN_TRADES = int(os.getenv('PAPER_MAX_OPEN_TRADES', '5'))
FEE_RATE = float(os.getenv('PAPER_FEE_PERCENT', '0.1')) / 100.0
SLIPPAGE_RATE = float(os.getenv('PAPER_SLIPPAGE_PERCENT', '0')) / 100.0
LIVE_TRADING_ENABLED = False
_lock = RLock()
_equity = STARTING_BALANCE
_trades: list[dict] = []


def _finite_positive(value: float) -> bool:
    return value > 0 and value == value and value != float('inf')


def _apply_entry_slippage(price: float) -> float:
    return price * (1 + SLIPPAGE_RATE)


def _apply_exit_slippage(price: float) -> float:
    return price * (1 - SLIPPAGE_RATE)


def account() -> dict:
    with _lock:
        open_trades = [trade for trade in _trades if trade['status'] == 'OPEN']
        closed_trades = [trade for trade in _trades if trade['status'] == 'CLOSED']
        open_exposure = sum(float(trade['entry']) * float(trade['quantity']) for trade in open_trades)
        fees_paid = sum(float(trade.get('fees') or 0) for trade in _trades)
        realized_pnl = sum(float(trade.get('pnl') or 0) for trade in closed_trades)
        return {
            'starting_balance': STARTING_BALANCE,
            'equity': round(_equity, 2),
            'realized_pnl': round(realized_pnl, 2),
            'risk_percent': RISK_PERCENT,
            'max_open_trades': MAX_OPEN_TRADES,
            'fee_percent_per_side': round(FEE_RATE * 100, 4),
            'slippage_percent_per_side': round(SLIPPAGE_RATE * 100, 4),
            'open_trades': len(open_trades),
            'closed_trades': len(closed_trades),
            'open_exposure': round(open_exposure, 2),
            'fees_paid': round(fees_paid, 4),
            'mode': 'PAPER ONLY',
            'live_trading_enabled': False,
        }


def open_trade(signal: dict) -> dict:
    global _equity
    if signal.get('signal') != 'BUY':
        raise ValueError('Paper entries require a BUY signal; SELL means exit/avoid, not short.')
    requested_entry, stop = float(signal['entry']), float(signal['sl'])
    if not _finite_positive(requested_entry) or not _finite_positive(stop):
        raise ValueError('Invalid entry or stop-loss')
    entry = _apply_entry_slippage(requested_entry)
    risk_per_unit = entry - stop
    if entry <= 0 or risk_per_unit <= 0:
        raise ValueError('Invalid entry or stop-loss')
    symbol = str(signal['symbol']).upper()
    with _lock:
        if any(trade['status'] == 'OPEN' and trade['symbol'] == symbol for trade in _trades):
            raise ValueError(f'An open paper position already exists for {symbol}')
        open_trades = [trade for trade in _trades if trade['status'] == 'OPEN']
        if len(open_trades) >= MAX_OPEN_TRADES:
            raise ValueError(f'Maximum of {MAX_OPEN_TRADES} open paper trades reached')
        invested = sum(float(t['entry']) * float(t['quantity']) for t in open_trades)
        available = max(_equity - invested, 0.0)
        if available <= 0:
            raise ValueError('Insufficient virtual balance for another spot position')
        # Keep the entry fee inside the available cash so a position can never
        # spend more than the virtual account owns.
        risk_budget = available * RISK_PERCENT / 100
        max_quantity_by_cash = available / (entry * (1 + FEE_RATE))
        qty = round(min(risk_budget / risk_per_unit, max_quantity_by_cash), 8)
        if qty <= 0:
            raise ValueError('Calculated paper position size is too small')
        entry_fee = round(entry * qty * FEE_RATE, 8)
        _equity -= entry_fee
        trade = {
            'id': len(_trades) + 1,
            'symbol': symbol,
            'entry': round(entry, 8),
            'requested_entry': round(requested_entry, 8),
            'quantity': qty,
            'sl': stop,
            'tp1': signal['tp1'],
            'tp2': signal['tp2'],
            'tp3': signal['tp3'],
            'status': 'OPEN',
            'opened_at': datetime.now(timezone.utc).isoformat(),
            'mode': 'PAPER',
            'fee_rate_per_side': FEE_RATE,
            'slippage_rate_per_side': SLIPPAGE_RATE,
            'entry_fee': entry_fee,
            'fees': entry_fee,
        }
        _trades.append(trade)
        return trade.copy()


def list_trades() -> list[dict]:
    with _lock:
        return [trade.copy() for trade in reversed(_trades)]


def update_prices(prices: dict[str, float]) -> int:
    """Close paper BUY trades when the latest market price reaches SL or TP1.

    If both levels are somehow crossed between refreshes, the stop is applied
    first. Fees and slippage are deducted from the reported result.
    """
    closed = 0
    with _lock:
        global _equity
        for trade in _trades:
            if trade['status'] != 'OPEN':
                continue
            price = prices.get(trade['symbol'])
            if price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if not _finite_positive(price):
                continue
            exit_reason = None
            if price <= float(trade['sl']):
                exit_reason = 'STOP_LOSS'
            elif price >= float(trade['tp1']):
                exit_reason = 'TP1'
            if exit_reason is None:
                continue
            exit_price = _apply_exit_slippage(price)
            gross_pnl = (exit_price - float(trade['entry'])) * float(trade['quantity'])
            exit_fee = round(exit_price * float(trade['quantity']) * FEE_RATE, 8)
            net_pnl = gross_pnl - exit_fee
            _equity += gross_pnl - exit_fee
            trade.update({
                'status': 'CLOSED',
                'exit': round(exit_price, 8),
                'requested_exit': round(price, 8),
                'gross_pnl': round(gross_pnl, 8),
                'exit_fee': exit_fee,
                'fees': round(float(trade.get('entry_fee') or 0) + exit_fee, 8),
                'pnl': round(net_pnl, 2),
                'exit_reason': exit_reason,
                'closed_at': datetime.now(timezone.utc).isoformat(),
            })
            closed += 1
    return closed
