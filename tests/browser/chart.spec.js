// Browser-level verification of the live chart.
//
// This file exists because the development sandbox has no browser and cannot
// reach Binance. GitHub Actions has both, so these assertions run there against
// real market data. Every test fails loudly rather than skipping.
const { test, expect } = require('@playwright/test');

const BADGE_STATES = [
  'CONNECTING',
  'STREAM OPEN',
  'LIVE',
  'RECONNECTING',
  'REST ONLY',
  'STALE',
  'OFFLINE',
  'PAUSED',
  'DATA ERROR',
];

async function loadChart(page) {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  await page.goto('/chart', { waitUntil: 'domcontentloaded' });
  // The REST load is what populates the candle count.
  try {
    await expect(page.locator('#candleCount')).not.toHaveText('—', { timeout: 90000 });
  } catch (error) {
    const badge = await page.locator('#connBadge').innerText().catch(() => 'unavailable');
    const status = await page.locator('#statusText').innerText().catch(() => 'unavailable');
    const library = await page.evaluate(() => Boolean(window.LightweightCharts)).catch(() => false);
    throw new Error(
      `Chart never rendered candles. badge="${badge}" status="${status}" ` +
      `chart_library_loaded=${library} page_errors=${JSON.stringify(pageErrors)}`
    );
  }
  return pageErrors;
}

test('loads real candles from a named provider and renders a valid chart', async ({ page }) => {
  const pageErrors = await loadChart(page);

  const count = Number(await page.locator('#candleCount').innerText());
  expect(count).toBeGreaterThanOrEqual(35);
  const source = await page.locator('#dataSource').innerText();
  expect(source).toMatch(/binance|coinbase/i);
  expect(source).toMatch(/5m/);

  const price = await page.locator('#currentPrice').innerText();
  expect(Number(price.replace(/,/g, ''))).toBeGreaterThan(0);

  await expect(page.locator('#lastUpdate')).not.toHaveText('—');
  await expect(page.locator('#chartTitle')).toContainText('BTCUSDT · 5m');
  await expect(page.locator('#candleState')).toHaveText(/FORMING CANDLE|LAST CANDLE CLOSED/);

  await page.screenshot({ path: 'evidence/chart-5m.png' });
  expect(pageErrors).toEqual([]);
});

test('never claims LIVE unless a websocket actually opened', async ({ page }) => {
  await loadChart(page);

  // Give the socket a chance to connect, then read the truth from the page.
  await page.waitForTimeout(4000);
  const badge = (await page.locator('#connBadge').innerText()).trim();
  const wsOpened = await page.evaluate(() => window.__chartDebug.wsEverOpened());
  const snapshot = await page.evaluate(() => window.__chartDebug.snapshot());

  expect(BADGE_STATES).toContain(badge);
  expect(snapshot.candles).toBeGreaterThanOrEqual(35);

  if (wsOpened) {
    expect(['LIVE', 'STREAM OPEN', 'STALE', 'RECONNECTING', 'REST ONLY']).toContain(badge);
  } else {
    // The whole point: a REST-only chart must never display LIVE.
    expect(badge).not.toBe('LIVE');
    expect(await page.locator('#statusText').innerText()).not.toMatch(/\bLIVE\b/);
  }
});

test('serves uniform, interval-aligned candles and never mislabels the provider', async ({ page }) => {
  await loadChart(page);

  const verified = [];

  for (const [interval, seconds] of [['5m', 300], ['1h', 3600], ['1d', 86400]]) {
    const result = await page.evaluate(async (value) => {
      const response = await fetch(`/api/candles/BTCUSDT?interval=${value}&limit=120`);
      return { status: response.status, body: await response.json() };
    }, interval);

    expect(result.status, `${interval} request failed`).toBe(200);
    expect(result.body.interval).toBe(interval);
    // The response must name the provider that actually served the data.
    expect(result.body.provider, `${interval} provider`).toBeTruthy();

    const times = result.body.candles.map((candle) => candle.time);
    expect(times.length).toBeGreaterThanOrEqual(35);

    const deltas = new Set(
      times.slice(1).map((time, index) => Math.round(time - times[index]))
    );
    expect([...deltas], `${interval} candle spacing`).toEqual([seconds]);
    expect(
      times.every((time) => time % seconds === 0),
      `${interval} timestamps must align to ${seconds}s`
    ).toBe(true);

    verified.push(`${interval}=${seconds}s via ${result.body.provider}`);
  }

  console.log('verified intervals: ' + verified.join(', '));

  // 4h is not served by every provider. Either it works, or it fails clearly.
  const gap = await page.evaluate(async () => {
    const response = await fetch('/api/candles/BTCUSDT?interval=4h&limit=60');
    return { status: response.status, body: await response.json() };
  });
  if (gap.status === 200) {
    const times = gap.body.candles.map((candle) => candle.time);
    const deltas = new Set(times.slice(1).map((time, i) => Math.round(time - times[i])));
    expect([...deltas], '4h candle spacing').toEqual([14400]);
  } else {
    expect([400, 502]).toContain(gap.status);
    expect(String(gap.body.detail || '').length).toBeGreaterThan(10);
  }
});

test('switching timeframe reloads data and updates every label', async ({ page }) => {
  await loadChart(page);

  await page.locator('#interval').selectOption('1h');
  await expect(page.locator('#metaInterval')).toHaveText('1h');
  await expect(page.locator('#dataSource')).toHaveText(/1h/);
  await expect(page.locator('#chartTitle')).toContainText('BTCUSDT · 1h');
  await expect(page.locator('#candleCount')).not.toHaveText('—');
  await page.screenshot({ path: 'evidence/chart-1h.png' });

  await page.locator('#interval').selectOption('5m');
  await expect(page.locator('#metaInterval')).toHaveText('5m');
  await expect(page.locator('#dataSource')).toHaveText(/5m/);
  await page.screenshot({ path: 'evidence/chart-5m-switched.png' });
});

test('a manual refresh keeps the zoomed viewport instead of resetting it', async ({ page }) => {
  await loadChart(page);
  await page.waitForTimeout(1500);

  const box = await page.locator('#chart').boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(800);

  const before = await page.evaluate(() => window.__chartDebug.visibleRange());
  expect(before).not.toBeNull();

  await page.locator('#load').click();
  await page.waitForTimeout(2500);

  const after = await page.evaluate(() => window.__chartDebug.visibleRange());
  expect(after).not.toBeNull();

  // Allow a bar of drift, but a full reset would be far larger than this.
  expect(Math.abs(after.from - before.from)).toBeLessThanOrEqual(2);
  expect(Math.abs(after.to - before.to)).toBeLessThanOrEqual(2);
});
