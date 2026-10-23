'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const Core = require('./chart-core.js');

function kline(overrides) {
  const base = {
    e: 'kline',
    E: 1700000042000,
    s: 'BTCUSDT',
    k: {
      t: 1700000040000,
      T: 1700000099999,
      s: 'BTCUSDT',
      i: '1m',
      o: '100.0',
      h: '101.0',
      l: '99.5',
      c: '100.5',
      v: '12.5',
      x: false,
    },
  };
  return Object.assign(base, overrides || {});
}

function restRow(time, overrides) {
  return Object.assign({
    time,
    open: 100,
    high: 101,
    low: 99,
    close: 100.5,
    volume: 10,
    signal: 'WAIT',
  }, overrides || {});
}

test('intervalSeconds covers every supported timeframe exactly', () => {
  assert.deepEqual(Object.keys(Core.INTERVAL_SECONDS), [
    '1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '1d',
  ]);
  assert.equal(Core.intervalSeconds('1h'), 3600);
  assert.equal(Core.intervalSeconds('5m'), 300);
  assert.equal(Core.intervalSeconds('7m'), null);
});

test('normalizeKline accepts a valid 1h candle only on an hour boundary', () => {
  const valid = kline();
  valid.k.t = 1700006400000; // 2023-11-15T00:00:00Z
  valid.k.i = '1h';
  const normalized = Core.normalizeKline(JSON.stringify(valid), {
    symbol: 'btcusdt', interval: '1h',
  });
  assert.equal(normalized.time, 1700006400);
  assert.equal(normalized.interval, '1h');
  assert.equal(normalized.closed, false);
  assert.equal(normalized.open, 100);

  const misaligned = kline();
  misaligned.k.t = 1700006700000; // 00:05 UTC, invalid for 1h
  misaligned.k.i = '1h';
  assert.equal(Core.normalizeKline(misaligned, { symbol: 'BTCUSDT', interval: '1h' }), null);
});

test('a relayed kline keeps the provider that produced it', () => {
  const withProvider = kline();
  withProvider.k.provider = 'coinbase:BTC-USD';
  const normalized = Core.normalizeKline(withProvider);
  assert.equal(normalized.provider, 'coinbase:BTC-USD');

  // A provider that is missing or not a string must not be invented.
  assert.equal(Core.normalizeKline(kline()).provider, null);
  const numeric = kline();
  numeric.k.provider = 42;
  assert.equal(Core.normalizeKline(numeric).provider, null);
});

test('normalizeKline rejects wrong symbol, wrong interval and inconsistent OHLC', () => {
  assert.equal(Core.normalizeKline(kline(), { symbol: 'ETHUSDT', interval: '1m' }), null);
  assert.equal(Core.normalizeKline(kline(), { symbol: 'BTCUSDT', interval: '5m' }), null);

  const badHigh = kline();
  badHigh.k.h = '98';
  assert.equal(Core.normalizeKline(badHigh), null);

  const negative = kline();
  negative.k.c = '-1';
  assert.equal(Core.normalizeKline(negative), null);

  assert.equal(Core.normalizeKline('{not json'), null);
  assert.equal(Core.normalizeKline({ e: 'trade' }), null);
});

test('normalizeKline supports combined-stream envelopes and marks closed candles', () => {
  const closed = kline();
  closed.k.x = true;
  const wrapped = { stream: 'btcusdt@kline_1m', data: closed };
  const normalized = Core.normalizeKline(wrapped, { symbol: 'BTCUSDT', interval: '1m' });
  assert.equal(normalized.closed, true);
  assert.equal(normalized.symbol, 'BTCUSDT');
});

test('buildCandleSeries sorts, de-duplicates and reports 1h spacing', () => {
  const rows = [
    restRow(1700010000),
    restRow(1700006400),
    restRow(1700006400, { close: 100.75 }),
    restRow(1700006500), // misaligned for 1h
    restRow(1700007000, { high: 1 }), // malformed
  ];
  const result = Core.buildCandleSeries(rows, '1h', { now: 1700015000 });
  assert.deepEqual(result.candles.map((row) => row.time), [1700006400, 1700010000]);
  assert.equal(result.candles[0].close, 100.75);
  assert.equal(result.intervalSeconds, 3600);
  assert.equal(result.spacingOk, true);
  assert.equal(result.forming, false);
  assert.equal(result.rejected.misaligned, 1);
  assert.equal(result.rejected.invalid, 1);
  assert.equal(result.rejected.duplicate, 1);
  assert.ok(result.warnings.some((warning) => warning.includes('misaligned') || warning.includes('did not align')));
});

test('buildCandleSeries flags a gap and the currently forming candle', () => {
  const rows = [restRow(0), restRow(300), restRow(900), restRow(1200)];
  const result = Core.buildCandleSeries(rows, '5m', { now: 1210 });
  assert.equal(result.spacingOk, false);
  assert.equal(result.spacingMismatches, 1);
  assert.equal(result.largestGapSeconds, 600);
  assert.equal(result.forming, true);
  assert.equal(result.completedCount, 3);
});

test('buildCandleSeries rejects unsupported intervals and empty payloads', () => {
  assert.throws(() => Core.buildCandleSeries([], '7m'), /Unsupported interval/);
  assert.throws(() => Core.buildCandleSeries([], '5m'), /No valid candles/);
  assert.throws(() => Core.buildCandleSeries({}, '5m'), /list/);
});

test('upsertCandle replaces in place, appends chronological candles and keeps signals', () => {
  const candles = [restRow(0, { signal: 'BUY' }), restRow(300, { signal: 'SELL' })];
  const replaced = Core.upsertCandle(candles, restRow(300, { close: 101 }), 2);
  assert.equal(replaced.changed, true);
  assert.equal(replaced.replaced, true);
  assert.equal(candles[1].close, 101);
  assert.equal(candles[1].signal, 'SELL');

  const appended = Core.upsertCandle(candles, restRow(600), 2);
  assert.equal(appended.changed, true);
  assert.equal(appended.appended, true);
  assert.deepEqual(candles.map((row) => row.time), [300, 600]);

  const old = Core.upsertCandle(candles, restRow(0), 2);
  assert.equal(old.changed, false);
  assert.equal(old.reason, 'out-of-order');
});

test('toChartCandle colours signal candles and preserves candle direction otherwise', () => {
  assert.deepEqual(Core.styleForSignal('BUY', 100, 99), {
    color: '#facc15', borderColor: '#facc15', wickColor: '#facc15',
  });
  assert.equal(Core.styleForSignal('WAIT', 100, 99).color, '#fb7185');
  assert.equal(Core.styleForSignal('WAIT', 99, 100).color, '#36d399');
  assert.equal(Core.toChartCandle(restRow(0), true).signal, 'WAIT');
});

test('emaSeries produces one point per candle', () => {
  const rows = [restRow(0, { close: 100.5 }), restRow(60, { close: 101 }), restRow(120, { close: 103 })];
  const points = Core.emaSeries(rows, 2);
  assert.equal(points.length, 3);
  assert.equal(points[0].value, 100.5);
  assert.ok(points[2].value > 100.5);
  assert.deepEqual(Core.emaSeries([], 9), []);
  assert.deepEqual(Core.emaSeries(rows, 0), []);
});

test('isStale honours the threshold', () => {
  assert.equal(Core.isStale(null, 1000, 30000), true);
  assert.equal(Core.isStale(900, 1000, 30000), false);
  assert.equal(Core.isStale(0, 60000, 30000), true);
});

class FakeSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.sent = [];
    FakeSocket.instances.push(this);
  }

  open() {
    this.readyState = 1;
    if (this.onopen) this.onopen({});
  }

  message(payload) {
    if (this.readyState !== 1) return;
    if (this.onmessage) this.onmessage({ data: typeof payload === 'string' ? payload : JSON.stringify(payload) });
  }

  fail() {
    if (this.onerror) this.onerror({});
  }

  close(code, reason) {
    if (this.readyState === 3) return;
    this.readyState = 3;
    if (this.onclose) this.onclose({ code: code || 1000, reason: reason || '' });
  }
}

function makeHarness(options) {
  FakeSocket.instances = [];
  const timers = [];
  const states = [];
  const candles = [];
  const errors = [];
  let now = 1700000002000;
  const stream = new Core.LiveStream(Object.assign({
    WebSocketImpl: FakeSocket,
    setTimeoutImpl: (fn, delay) => {
      const timer = { fn, delay };
      timers.push(timer);
      return timer;
    },
    clearTimeoutImpl: (timer) => {
      const index = timers.indexOf(timer);
      if (index >= 0) timers.splice(index, 1);
    },
    now: () => now,
    baseDelayMs: 1000,
    maxDelayMs: 30000,
    onState: (state) => states.push(state.state),
    onCandle: (candle) => candles.push(candle),
    onError: (error) => errors.push(error),
  }, options || {}));
  return {
    stream,
    timers,
    states,
    candles,
    errors,
    advance: (ms) => { now += ms; },
    runNextTimer: () => {
      const timer = timers.shift();
      if (!timer) throw new Error('No scheduled timer');
      timer.fn();
      return timer;
    },
    sockets: FakeSocket.instances,
  };
}

test('LiveStream connects to the selected symbol and interval', () => {
  const harness = makeHarness();
  harness.stream.start('btcusdt', '1h');
  assert.equal(harness.sockets.length, 1);
  assert.equal(
    harness.sockets[0].url,
    'wss://stream.binance.com:9443/ws/btcusdt@kline_1h',
  );
  assert.equal(harness.stream.snapshot().state, 'connecting');
});

test('LiveStream ignores duplicate starts and stale callbacks after a switch', () => {
  const harness = makeHarness();
  harness.stream.start('BTCUSDT', '5m');
  const first = harness.sockets[0];
  harness.stream.start('BTCUSDT', '5m');
  assert.equal(harness.sockets.length, 1);

  first.open();
  first.message(Object.assign(kline(), { k: Object.assign({}, kline().k, { t: 1700000100000, i: '5m' }) }));
  assert.equal(harness.candles.length, 1);
  assert.equal(harness.stream.snapshot().state, 'live');

  harness.stream.start('ETHUSDT', '15m');
  assert.equal(harness.sockets.length, 2);
  assert.equal(first.readyState, 3);
  assert.equal(
    harness.sockets[1].url,
    'wss://stream.binance.com:9443/ws/ethusdt@kline_15m',
  );

  // A late message from the closed socket must not touch the new chart.
  first.message(kline());
  assert.equal(harness.candles.length, 1);
});

test('LiveStream reconnects with exponential backoff and gives up when bounded', () => {
  const harness = makeHarness({ maxRetries: 2 });
  harness.stream.start('BTCUSDT', '5m');
  const first = harness.sockets[0];
  first.open();
  first.close(1006, 'network reset');

  assert.equal(harness.stream.snapshot().state, 'reconnecting');
  assert.equal(harness.stream.snapshot().retries, 1);
  assert.equal(harness.timers[0].delay, 1000);

  harness.runNextTimer();
  const second = harness.sockets[1];
  assert.equal(second.url, first.url);
  second.open();
  // A socket that opens but never sends data keeps the retry budget.
  second.close(1006, 'network reset');
  assert.equal(harness.stream.snapshot().retries, 2);
  assert.equal(harness.timers[0].delay, 2000);

  harness.runNextTimer();
  const third = harness.sockets[2];
  third.close(1006, 'network reset');
  assert.equal(harness.stream.snapshot().state, 'offline');
  assert.equal(harness.stream.snapshot().retries, 2);
  assert.equal(harness.timers.length, 0);
});

test('LiveStream resets the retry budget after a valid message', () => {
  const harness = makeHarness({ maxRetries: 2 });
  harness.stream.start('BTCUSDT', '5m');
  harness.sockets[0].open();
  harness.sockets[0].message(Object.assign(kline(), { k: Object.assign({}, kline().k, { t: 1700000100000, i: '5m' }) }));
  assert.equal(harness.stream.snapshot().retries, 0);

  harness.sockets[0].close(1006, 'reset');
  assert.equal(harness.stream.snapshot().retries, 1);
  harness.runNextTimer();
  harness.sockets[1].open();
  harness.sockets[1].message(Object.assign(kline(), { k: Object.assign({}, kline().k, { t: 1700000400000, i: '5m' }) }));
  assert.equal(harness.stream.snapshot().retries, 0);
  assert.equal(harness.stream.snapshot().state, 'live');
});

test('LiveStream ignores malformed messages and reports them', () => {
  const harness = makeHarness();
  harness.stream.start('BTCUSDT', '1h');
  const socket = harness.sockets[0];
  socket.open();
  socket.message(kline()); // 1m timestamp is valid but interval is wrong for this stream
  assert.equal(harness.candles.length, 0);
  assert.equal(harness.errors.length, 1);
  assert.equal(harness.stream.snapshot().state, 'connected');

  const valid = kline();
  valid.k.t = 1700006400000;
  valid.k.i = '1h';
  socket.message(valid);
  assert.equal(harness.candles.length, 1);
  assert.equal(harness.stream.snapshot().state, 'live');
});

test('LiveStream stop tears down the socket and cancels reconnects', () => {
  const harness = makeHarness();
  harness.stream.start('BTCUSDT', '5m');
  harness.sockets[0].open();
  harness.sockets[0].close(1006, 'reset');
  assert.equal(harness.timers.length, 1);

  harness.stream.stop();
  assert.equal(harness.timers.length, 0);
  assert.equal(harness.stream.snapshot().state, 'stopped');
  assert.equal(harness.stream.snapshot().symbol, null);
});

test('LiveStream reports no data as stale even while the socket is open', () => {
  const harness = makeHarness();
  harness.stream.start('BTCUSDT', '1m');
  harness.sockets[0].open();
  assert.equal(harness.stream.isStaleAt(1700000002000 + 1000, 30000), true);

  harness.sockets[0].message(kline());
  assert.equal(harness.stream.isStaleAt(1700000002000 + 1000, 30000), false);
  assert.equal(harness.stream.isStaleAt(1700000002000 + 60000, 30000), true);
});

test('host timer functions are wrapped so they are never invoked as object methods', () => {
  const stream = new Core.LiveStream({ WebSocketImpl: function () {}, setTimeoutImpl: null });
  // In browsers, calling window.setTimeout with a foreign `this` throws
  // "Illegal invocation". The defaults must therefore be wrappers, not the
  // host functions themselves. Node does not enforce this, so assert identity.
  assert.notStrictEqual(stream.setTimeoutImpl, globalThis.setTimeout);
  assert.notStrictEqual(stream.clearTimeoutImpl, globalThis.clearTimeout);
  assert.notStrictEqual(stream.now, Date.now);
  assert.strictEqual(typeof stream.setTimeoutImpl, 'function');
  assert.strictEqual(typeof stream.now(), 'number');
});
