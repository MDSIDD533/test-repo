// Browser proof that replay mode renders recorded candles and never claims LIVE.
//
// Runs against a server started with MARKET_DATA_MODE=replay, so no exchange
// access is needed. If the page ever presents a recording as live market data,
// these assertions fail.
const { test, expect } = require('@playwright/test');

test('replay mode renders recorded candles and labels itself', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  await page.goto('/chart', { waitUntil: 'domcontentloaded' });

  // The banner is the promise that nothing on this page is live.
  await expect(page.locator('#replayBanner')).toBeVisible();
  await expect(page.locator('#replayBanner')).toContainText('REPLAY');
  await expect(page.locator('#replayBanner')).toContainText('NOT live');

  await expect(page.locator('#candleCount')).not.toHaveText('—', { timeout: 60000 });
  const count = Number(await page.locator('#candleCount').innerText());
  expect(count).toBeGreaterThanOrEqual(35);

  // Real recorded prices, not placeholders.
  const shownPrice = await page.locator('#chartTitle').innerText();
  expect(shownPrice).toMatch(/BTCUSDT/);

  // The connection badge must never read LIVE for a recording.
  const badge = await page.locator('#connBadge').innerText();
  expect(badge).not.toBe('LIVE');
  expect(badge).toBe('REPLAY');

  const source = await page.locator('#dataSource').innerText();
  expect(source).toMatch(/replay/i);

  expect(pageErrors).toEqual([]);
});

test('the dashboard shows the replay banner and the recorded scanner row', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await expect(page.locator('#replayBanner')).toBeVisible();
  await expect(page.locator('#replayBanner')).toContainText('REPLAY');

  const firstRow = page.locator('#scannerRows tr').first();
  await expect(firstRow).toContainText('BTCUSDT', { timeout: 60000 });
});

test('the API reports replay mode instead of claiming live data', async ({ request }) => {
  const health = await (await request.get('/api/health')).json();
  expect(health.data_mode).toBe('replay');
  expect(health.live_trading_enabled).toBe(false);
  expect(health.replay.providers.length).toBeGreaterThan(0);

  const candles = await (await request.get('/api/candles/BTCUSDT?interval=1h&limit=100')).json();
  expect(candles.provider).toMatch(/^replay:/);
  expect(candles.candles.length).toBeGreaterThanOrEqual(35);
});
