/* Chart core: pure helpers + a reconnecting Binance kline stream controller.
 *
 * This file has no DOM dependencies so it can be unit-tested with Node.
 * The browser page wires these helpers to lightweight-charts.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ChartCore = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const INTERVAL_SECONDS = {
    '1m': 60,
    '3m': 180,
    '5m': 300,
    '15m': 900,
    '30m': 1800,
    '1h': 3600,
    '2h': 7200,
    '4h': 14400,
    '1d': 86400,
  };

  const SIGNAL_COLORS = {
    BUY: '#facc15',
    SELL: '#60a5fa',
    WAIT: null,
  };

  function intervalSeconds(interval) {
    return Object.prototype.hasOwnProperty.call(INTERVAL_SECONDS, interval)
      ? INTERVAL_SECONDS[interval]
      : null;
  }

  function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function isFinitePositive(value) {
    return isFiniteNumber(value) && value > 0;
  }

  function toNumber(value) {
    if (typeof value === 'number') return value;
    if (typeof value === 'string' && value.trim() !== '') return Number(value);
    return NaN;
  }

  function normalizeCandleRow(row) {
    if (!row || typeof row !== 'object') return null;
    const time = toNumber(row.time);
    const open = toNumber(row.open);
    const high = toNumber(row.high);
    const low = toNumber(row.low);
    const close = toNumber(row.close);
    const volume = toNumber(row.volume);
    if (!isFiniteNumber(time) || time < 0) return null;
    if (![open, high, low, close].every(isFinitePositive)) return null;
    if (!isFiniteNumber(volume) || volume < 0) return null;
    if (high < Math.max(open, close, low)) return null;
    if (low > Math.min(open, close, high)) return null;
    return {
      time: Math.floor(time),
      open,
      high,
      low,
      close,
      volume,
      signal: typeof row.signal === 'string' ? row.signal : 'WAIT',
    };
  }

  function alignToInterval(seconds, interval) {
    const size = intervalSeconds(interval);
    if (!size || !isFiniteNumber(seconds)) return false;
    return Math.floor(seconds) % size === 0;
  }

  /* Convert a Binance kline WebSocket message into a validated candle.
   * Returns null for malformed, misaligned, or wrong-symbol/interval data.
   * `k.x` is preserved as `closed` to distinguish a forming candle.
   */
  function normalizeKline(payload, expected) {
    let message = payload;
    if (typeof message === 'string') {
      try {
        message = JSON.parse(message);
      } catch (error) {
        return null;
      }
    }
    if (!message || typeof message !== 'object') return null;
    const data = message.data && typeof message.data === 'object' ? message.data : message;
    const k = data.k;
    if (!k || typeof k !== 'object') return null;
    const eventType = typeof data.e === 'string' ? data.e : 'kline';
    if (eventType !== 'kline') return null;
    if (message.stream && typeof message.stream === 'string' && !String(message.stream).includes('@kline_')) {
      return null;
    }

    const symbol = String(k.s || '').toUpperCase();
    const interval = String(k.i || '');
    if (expected) {
      if (expected.symbol && symbol !== String(expected.symbol).toUpperCase()) return null;
      if (expected.interval && interval !== expected.interval) return null;
    }
    if (!intervalSeconds(interval)) return null;

    const startMs = toNumber(k.t);
    const open = toNumber(k.o);
    const high = toNumber(k.h);
    const low = toNumber(k.l);
    const close = toNumber(k.c);
    const volume = toNumber(k.v);
    if (!isFiniteNumber(startMs) || startMs <= 0) return null;
    if (![open, high, low, close].every(isFinitePositive)) return null;
    if (!isFiniteNumber(volume) || volume < 0) return null;
    if (high < Math.max(open, close, low)) return null;
    if (low > Math.min(open, close, high)) return null;

    const time = Math.floor(startMs / 1000);
    // A kline timestamp is the candle's opening time. Reject data that is not
    // aligned to the selected interval so a 1h chart can never render 5m bars.
    if (!alignToInterval(time, interval)) return null;

    return {
      time,
      open,
      high,
      low,
      close,
      volume,
      closed: k.x === true,
      symbol,
      interval,
      eventTime: isFiniteNumber(toNumber(k.E)) ? toNumber(k.E) : null,
    };
  }

  /* Build a chronological, de-duplicated candle series from REST rows. */
  function buildCandleSeries(rows, interval, options) {
    const settings = options || {};
    const size = intervalSeconds(interval);
    if (!size) throw new Error('Unsupported interval: ' + interval);
    if (!Array.isArray(rows)) throw new Error('Candle payload must be a list');

    const rejected = { invalid: 0, misaligned: 0, duplicate: 0 };
    const byTime = new Map();
    for (const row of rows) {
      const candle = normalizeCandleRow(row);
      if (!candle) {
        rejected.invalid += 1;
        continue;
      }
      if (!alignToInterval(candle.time, interval)) {
        rejected.misaligned += 1;
        continue;
      }
      if (byTime.has(candle.time)) rejected.duplicate += 1;
      byTime.set(candle.time, candle);
    }

    const candles = Array.from(byTime.values()).sort((a, b) => a.time - b.time);
    const warnings = [];
    if (rejected.invalid) warnings.push(rejected.invalid + ' malformed candle(s) ignored');
    if (rejected.misaligned) warnings.push(rejected.misaligned + ' candle(s) did not align to ' + interval + ' and were ignored');
    if (rejected.duplicate) warnings.push(rejected.duplicate + ' duplicate candle timestamp(s) collapsed');
    if (!candles.length) throw new Error('No valid candles in the ' + interval + ' payload');

    let spacingMismatches = 0;
    let largestGap = 0;
    for (let index = 1; index < candles.length; index += 1) {
      const delta = candles[index].time - candles[index - 1].time;
      if (delta !== size) {
        spacingMismatches += 1;
        largestGap = Math.max(largestGap, delta);
      }
    }
    if (spacingMismatches) {
      warnings.push(
        'Candle spacing differs from ' + interval + ' in ' + spacingMismatches +
        ' place(s); largest gap ' + largestGap + 's'
      );
    }

    const nowSeconds = isFiniteNumber(settings.now) ? settings.now : Date.now() / 1000;
    const last = candles[candles.length - 1];
    const forming = last.time + size > nowSeconds;
    return {
      candles,
      interval,
      intervalSeconds: size,
      spacingOk: spacingMismatches === 0,
      spacingMismatches,
      largestGapSeconds: largestGap,
      rejected,
      warnings,
      firstTime: candles[0].time,
      lastTime: last.time,
      forming,
      completedCount: candles.length - (forming ? 1 : 0),
    };
  }

  function styleForSignal(signal, open, close) {
    const signalColor = SIGNAL_COLORS[String(signal || '').toUpperCase()];
    if (signalColor) {
      return { color: signalColor, borderColor: signalColor, wickColor: signalColor };
    }
    const color = close >= open ? '#36d399' : '#fb7185';
    return { color, borderColor: color, wickColor: color };
  }

  function toChartCandle(candle, preserveSignal) {
    const signal = preserveSignal === false
      ? 'WAIT'
      : (candle.signal || 'WAIT');
    const style = styleForSignal(signal, candle.open, candle.close);
    return {
      time: candle.time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      color: style.color,
      borderColor: style.borderColor,
      wickColor: style.wickColor,
      signal,
    };
  }

  /* Insert or replace a candle while keeping strict chronological order.
   * Returns {changed, candle, replaced, appended, reason}.
   */
  function upsertCandle(candles, incoming, maxLength) {
    if (!Array.isArray(candles)) throw new Error('Candle list must be an array');
    if (!incoming || !isFiniteNumber(incoming.time)) {
      return { changed: false, reason: 'invalid', candles };
    }
    const index = candles.findIndex((item) => item.time === incoming.time);
    if (index >= 0) {
      const merged = Object.assign({}, candles[index], incoming, {
        signal: candles[index].signal || incoming.signal || 'WAIT',
      });
      candles[index] = merged;
      return { changed: true, candle: merged, replaced: true, appended: false, candles };
    }
    const last = candles[candles.length - 1];
    if (last && incoming.time < last.time) {
      return { changed: false, reason: 'out-of-order', candles };
    }
    if (last && incoming.time === last.time) {
      return { changed: false, reason: 'duplicate', candles };
    }
    const fresh = Object.assign({ signal: 'WAIT' }, incoming);
    candles.push(fresh);
    const limit = Number.isInteger(maxLength) && maxLength > 0 ? maxLength : null;
    while (limit && candles.length > limit) candles.shift();
    return { changed: true, candle: fresh, replaced: false, appended: true, candles };
  }

  function emaSeries(candles, period) {
    if (!Array.isArray(candles) || !candles.length || !(period > 0)) return [];
    const alpha = 2 / (period + 1);
    const output = [];
    let value = Number(candles[0].close);
    output.push({ time: candles[0].time, value });
    for (let index = 1; index < candles.length; index += 1) {
      const close = Number(candles[index].close);
      value = close * alpha + value * (1 - alpha);
      output.push({ time: candles[index].time, value });
    }
    return output;
  }

  function isStale(lastMessageAt, nowMs, thresholdMs) {
    const limit = isFiniteNumber(thresholdMs) ? thresholdMs : 30000;
    if (!isFiniteNumber(lastMessageAt)) return true;
    return nowMs - lastMessageAt > limit;
  }

  /* Live Binance kline stream with bounded exponential backoff.
   *
   * - Starting a different symbol/interval closes the previous socket.
   * - Callbacks from superseded sockets are ignored using a generation token.
   * - Retries stop after maxRetries consecutive failures; REST polling remains.
   */
  class LiveStream {
    constructor(options) {
      const settings = options || {};
      this.WebSocketImpl = settings.WebSocketImpl ||
        (typeof WebSocket !== 'undefined' ? WebSocket : null);
      this.setTimeoutImpl = settings.setTimeoutImpl || setTimeout;
      this.clearTimeoutImpl = settings.clearTimeoutImpl || clearTimeout;
      this.now = settings.now || Date.now;
      this.urlFor = settings.urlFor || function (symbol, interval) {
        return 'wss://stream.binance.com:9443/ws/' +
          String(symbol).toLowerCase() + '@kline_' + interval;
      };
      this.maxRetries = Number.isInteger(settings.maxRetries) ? settings.maxRetries : 8;
      this.baseDelayMs = Number.isFinite(settings.baseDelayMs) ? settings.baseDelayMs : 1000;
      this.maxDelayMs = Number.isFinite(settings.maxDelayMs) ? settings.maxDelayMs : 30000;
      this.onState = typeof settings.onState === 'function' ? settings.onState : function () {};
      this.onCandle = typeof settings.onCandle === 'function' ? settings.onCandle : function () {};
      this.onError = typeof settings.onError === 'function' ? settings.onError : function () {};

      this.symbol = null;
      this.interval = null;
      this.key = null;
      this.socket = null;
      this.state = 'idle';
      this.retries = 0;
      this.generation = 0;
      this.reconnectTimer = null;
      this.lastMessageAt = null;
      this.lastError = null;
    }

    snapshot() {
      return {
        state: this.state,
        symbol: this.symbol,
        interval: this.interval,
        retries: this.retries,
        lastMessageAt: this.lastMessageAt,
        lastError: this.lastError,
      };
    }

    emit(state, extra) {
      this.state = state;
      this.onState(Object.assign(this.snapshot(), extra || {}));
    }

    isLive() {
      return this.state === 'live';
    }

    isStaleAt(nowMs, thresholdMs) {
      return isStale(this.lastMessageAt, nowMs === undefined ? this.now() : nowMs, thresholdMs);
    }

    start(symbol, interval) {
      const normalized = String(symbol || '').trim().toUpperCase();
      if (!normalized || !intervalSeconds(interval)) {
        throw new Error('A valid symbol and interval are required');
      }
      const key = normalized + '@' + interval;
      if (key === this.key && this.socket && (this.socket.readyState === 0 || this.socket.readyState === 1)) {
        return this.snapshot();
      }
      this._teardown();
      this.symbol = normalized;
      this.interval = interval;
      this.key = key;
      this.retries = 0;
      this.lastMessageAt = null;
      this.lastError = null;
      const generation = ++this.generation;
      this.emit('connecting');
      this._connect(generation);
      return this.snapshot();
    }

    stop() {
      this._teardown();
      this.symbol = null;
      this.interval = null;
      this.key = null;
      this.emit('stopped');
    }

    _teardown() {
      this.generation += 1;
      if (this.reconnectTimer !== null) {
        this.clearTimeoutImpl(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      const socket = this.socket;
      this.socket = null;
      if (socket) {
        try {
          socket.close(1000, 'client stopped');
        } catch (error) {
          /* ignore close errors */
        }
      }
    }

    _connect(generation) {
      if (generation !== this.generation) return;
      if (!this.WebSocketImpl) {
        this.lastError = 'WebSocket is not supported in this environment';
        this.emit('offline', { reason: 'unsupported' });
        return;
      }
      const symbol = this.symbol;
      const interval = this.interval;
      let socket;
      try {
        socket = new this.WebSocketImpl(this.urlFor(symbol, interval));
      } catch (error) {
        this.lastError = String(error && error.message ? error.message : error);
        this._scheduleReconnect(generation);
        return;
      }
      this.socket = socket;

      socket.onopen = () => {
        if (generation !== this.generation || this.socket !== socket) return;
        this.emit('connected');
      };
      socket.onmessage = (event) => {
        if (generation !== this.generation || this.socket !== socket) return;
        const candle = normalizeKline(event && event.data, { symbol, interval });
        if (!candle) {
          this.onError({ type: 'invalid-message', message: 'Ignored a malformed or misaligned kline message' });
          return;
        }
        this.lastMessageAt = this.now();
        this.retries = 0;
        this.lastError = null;
        if (this.state !== 'live') this.emit('live');
        this.onCandle(candle);
      };
      socket.onerror = () => {
        if (generation !== this.generation || this.socket !== socket) return;
        this.lastError = 'WebSocket error';
        this.onError({ type: 'socket-error', message: 'Binance live stream reported an error' });
      };
      socket.onclose = (event) => {
        if (generation !== this.generation || this.socket !== socket) return;
        this.socket = null;
        if (this.state === 'stopped' || this.state === 'idle') return;
        this.lastError = event && event.reason ? String(event.reason) : 'socket closed';
        this._scheduleReconnect(generation);
      };
    }

    _scheduleReconnect(generation) {
      if (generation !== this.generation) return;
      if (this.retries >= this.maxRetries) {
        this.emit('offline', { reason: 'retry-limit', retries: this.retries });
        return;
      }
      this.retries += 1;
      const delay = Math.min(this.baseDelayMs * Math.pow(2, this.retries - 1), this.maxDelayMs);
      this.emit('reconnecting', { retries: this.retries, delayMs: delay });
      this.reconnectTimer = this.setTimeoutImpl(() => {
        this.reconnectTimer = null;
        if (generation !== this.generation) return;
        this._connect(generation);
      }, delay);
    }
  }

  return {
    INTERVAL_SECONDS,
    alignToInterval,
    buildCandleSeries,
    emaSeries,
    intervalSeconds,
    isStale,
    normalizeCandleRow,
    normalizeKline,
    styleForSignal,
    toChartCandle,
    upsertCandle,
    LiveStream,
  };
});
