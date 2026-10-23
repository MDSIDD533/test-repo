// Proof that live candles reach the chart over a real WebSocket.
//
// The chart no longer connects to an exchange directly: it connects to the
// app's own /ws/candles relay, which tries Binance and falls back to Coinbase.
// On GitHub runners Binance answers HTTP 451, so this test is the evidence that
// the WebSocket path works end to end for at least one live provider.
const { test, expect } = require('@playwright/test');

test('a live websocket actually updates the chart with real candles', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));

  await page.goto('/chart', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#candleCount')).not.toHaveText('—', { timeout: 90000 });

  // The socket must genuinely open (the page never claims LIVE before this).
  await expect
    .poll(async () => page.evaluate(() => Boolean(window.__chartDebug && window.__chartDebug.wsEverOpened())), {
      timeout: 45000,
      message: 'the websocket to /ws/candles never opened',
    })
    .toBe(true);

  // A message must actually change the chart, not merely connect.
  await expect
    .poll(async () => page.evaluate(() => window.__chartDebug.snapshot().liveUpdates), {
      timeout: 90000,
      message: 'no live kline ever updated the chart',
    })
    .toBeGreaterThan(0);

  const snapshot = await page.evaluate(() => window.__chartDebug.snapshot());
  expect(snapshot.connection).toBe('live');
  // The provider is named, and it is a real one — never "unknown".
  expect(snapshot.provider).toBeTruthy();
  expect(snapshot.provider).toMatch(/binance|coinbase/);

  const source = await page.locator('#dataSource').innerText();
  expect(source).toMatch(/binance|coinbase/i);
  expect(source).toMatch(/live/i);

  const badge = await page.locator('#connBadge').innerText();
  expect(badge).toBe('LIVE');
  expect(pageErrors).toEqual([]);
});

test('the relay names the provider that actually served the stream', async ({ request }) => {
  // Server-side truth: ask the relay what it is doing for this symbol.
  const status = await (await request.get('/api/stream/providers?symbol=BTCUSDT')).json();
  expect(status.candidates).toContain('binance');
  expect(status.candidates).toContain('coinbase');
});
