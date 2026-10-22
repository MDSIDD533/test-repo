// Browser verification of the dashboard's honesty requirements:
// real stage state, a recorded failed experiment, and a persisted paper record.
const { test, expect } = require('@playwright/test');

const IGNORABLE_CONSOLE = [
  /Failed to load resource.*favicon/i,
  /net::ERR_BLOCKED_BY_CLIENT/i,
];

test('dashboard renders without JavaScript errors and labels the recorded experiment', async ({ page }) => {
  const pageErrors = [];
  const consoleErrors = [];
  page.on('pageerror', (error) => pageErrors.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error' && !IGNORABLE_CONSOLE.some((pattern) => pattern.test(message.text()))) {
      consoleErrors.push(message.text());
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });

  // The recorded experiment is loaded from the repository report.
  await expect(page.locator('#evalStatus')).toContainText('DO_NOT_DEPLOY', { timeout: 60000 });
  await expect(page.locator('#evalStatus')).toContainText('39610582675');
  await expect(page.locator('#evalMetrics')).toContainText('Candidate beat baseline');

  // The ledger panel must show real storage state, not a placeholder.
  await expect(page.locator('#ledgerStatus')).toContainText('Storage:', { timeout: 60000 });

  // The paper panel must disclose where state lives.
  await expect(page.locator('#paperStateNote')).not.toHaveText('');

  // Safety copy must be present.
  await expect(page.locator('#safetyText')).toContainText(/No live orders/i);

  await page.screenshot({ path: 'evidence/dashboard.png', fullPage: true });
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});

test('running the agent pipeline in the browser shows real stage statuses', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  await page.locator('#agentInterval').selectOption('5m');
  await page.locator('#runAgents').click();

  // Either the pipeline completes (with a NO TRADE decision) or a provider is
  // unreachable and the page says so. Both are honest; a fake success is not.
  await expect(page.locator('#agentStatus')).toContainText(/Overall:|failed|unavailable/i, {
    timeout: 120000,
  });

  const statusText = await page.locator('#agentStatus').innerText();
  if (/Overall:/i.test(statusText)) {
    expect(statusText).toMatch(/NO TRADE/);
    await expect(page.locator('#agentCards')).toContainText(/Prediction/i);
    await expect(page.locator('#agentCards')).toContainText(/NOT FROZEN|Frozen ledger id/i);
  } else {
    expect(statusText).toMatch(/failed|unavailable/i);
  }

  await page.screenshot({ path: 'evidence/dashboard-agents.png', fullPage: true });
});
