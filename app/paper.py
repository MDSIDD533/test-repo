from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
import tempfile

STARTING_BALANCE = float(os.getenv('PAPER_STARTING_BALANCE', '1000'))
RISK_PERCENT = float(os.getenv('PAPER_RISK_PERCENT', '1'))
MAX_OPEN_TRADES = int(os.getenv('PAPER_MAX_OPEN_TRADES', '5'))
FEE_RATE = float(os.getenv('PAPER_FEE_PERCENT', '0.1')) / 100.0
SLIPPAGE_RATE = float(os.getenv('PAPER_SLIPPAGE_PERCENT', '0')) / 100.0
LIVE_TRADING_ENABLED = False
_lock = RLock()
_equity = STARTING_BALANCE
_trades: list[dict] = []
STATE_PATH_ENV = 'PAPER_STATE_PATH'
STATE_VERSION = 1
_loaded = False
_load_warning: str | None = None
_persist_warning: str | None = None
_REQUIRED_TRADE_FIELDS = ('id', 'symbol', 'entry', 'quantity', 'sl', 'tp1', 'status')


class PaperStateError(ValueError):
    """Raised when a paper state file exists but cannot be trusted."""


def state_path() -> Path:
    """Resolve the state path at call time so tests and deployments can override it."""
    configured = os.getenv(STATE_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parent.parent / 'runtime' / 'paper_state.json'


def _state_problem() -> str | None:
    if _load_warning and _persist_warning:
        return _load_warning + ' ' + _persist_warning
    return _load_warning or _persist_warning


def _validate_state(payload: object) -> tuple[float, list[dict]]:
    """Validate a persisted account before trusting it.

    A corrupt file is never silently replaced by a fresh balance, because that
    would look like an unexplained loss of paper history.
    """
    if not isinstance(payload, dict):
        raise PaperStateError('state must be a JSON object')
    equity = payload.get('equity')
    if isinstance(equity, bool) or not isinstance(equity, (int, float)):
        raise PaperStateError('equity must be a number')
    equity = float(equity)
    if not _finite_positive(equity) and equity != 0:
        raise PaperStateError('equity must be finite and non-negative')
    trades = payload.get('trades')
    if not isinstance(trades, list):
        raise PaperStateError('trades must be a list')
    for index, trade in enumerate(trades):
        if not isinstance(trade, dict):
            raise PaperStateError(f'trade {index} must be an object')
        missing = [field for field in _REQUIRED_TRADE_FIELDS if field not in trade]
        if missing:
            raise PaperStateError(f'trade {index} is missing {missing}')
        if trade['status'] not in {'OPEN', 'CLOSED'}:
            raise PaperStateError(f"trade {index} has an unknown status {trade['status']!r}")
        for field in ('entry', 'quantity', 'sl'):
            value = trade[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PaperStateError(f'trade {index} field {field} must be a number')
            if not _finite_positive(float(value)):
                raise PaperStateError(f'trade {index} field {field} must be positive')
        if not str(trade.get('symbol') or '').strip():
            raise PaperStateError(f'trade {index} has an empty symbol')
    return equity, [dict(trade) for trade in trades]


def _reset_memory() -> None:
    global _equity, _trades
    _equity = STARTING_BALANCE
    _trades = []


def _load_locked() -> None:
    global _loaded, _load_warning, _equity, _trades
    path = state_path()
    if not path.exists():
        _reset_memory()
        _load_warning = None
        _loaded = True
        return
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        equity, trades = _validate_state(payload)
    except (OSError, ValueError) as exc:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        quarantine = path.with_name(path.name + '.corrupt-' + stamp)
        try:
            path.replace(quarantine)
            preserved = f'The unreadable file was preserved as {quarantine.name}.'
        except OSError:
            preserved = 'The unreadable file could not be preserved.'
        _reset_memory()
        _load_warning = (
            f'Paper state could not be loaded ({type(exc).__name__}: {exc}); '
            f'the account started fresh. {preserved}'
        )
        _loaded = True
        return
    _equity = equity
    _trades = trades
    _load_warning = None
    _loaded = True


def _ensure_loaded() -> None:
    global _loaded
    with _lock:
        if not _loaded:
            _load_locked()


def reload_state() -> dict:
    """Re-read the state file. Used on process start and by tests."""
    global _loaded
    with _lock:
        _loaded = False
        _load_locked()
        return {
            'state_file': str(state_path()),
            'equity': round(_equity, 2),
            'trades': len(_trades),
            'warning': _load_warning,
        }


def _persist_locked() -> None:
    """Atomically write the account. A failure is surfaced, never hidden."""
    global _persist_warning
    path = state_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': STATE_VERSION,
            'saved_at': datetime.now(timezone.utc).isoformat(),
            'equity': _equity,
            'trades': _trades,
        }
        descriptor, temporary = tempfile.mkstemp(
            dir=str(path.parent), prefix='.paper-state-', suffix='.tmp'
        )
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, sort_keys=True, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _persist_warning = None
    except OSError as exc:
        _persist_warning = (
            f'Paper state could not be saved to {path}: {type(exc).__name__}: {exc}. '
            'The in-memory account is still correct for this process.'
        )
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _finite_positive(value: float) -> bool:
    return value > 0 and value == value and value != float('inf')


def _apply_entry_slippage(price: float) -> float:
    return price * (1 + SLIPPAGE_RATE)


def _apply_exit_slippage(price: float) -> float:
    return price * (1 - SLIPPAGE_RATE)


def account() -> dict:
    _ensure_loaded()
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
            'state_file': str(state_path()),
            'persisted': True,
            'state_warning': _state_problem(),
        }


def open_trade(signal: dict) -> dict:
    global _equity
    _ensure_loaded()
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
        _persist_locked()
        return trade.copy()


def list_trades() -> list[dict]:
    _ensure_loaded()
    with _lock:
        return [trade.copy() for trade in reversed(_trades)]


def update_prices(prices: dict[str, float]) -> int:
    """Close paper BUY trades when the latest market price reaches SL or TP1.

    If both levels are somehow crossed between refreshes, the stop is applied
    first. Fees and slippage are deducted from the reported result.
    """
    _ensure_loaded()
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
        if closed:
            _persist_locked()
    return closed
