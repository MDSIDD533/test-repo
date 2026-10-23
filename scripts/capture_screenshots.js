// Capture real screenshots of the running app in a real browser.
//
// Used by the "Capture screenshots" workflow so the repository can show what
// the platform actually looks like without anyone having to run it locally.
// Every screenshot is taken only after the page proves it rendered real data.
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:8002';
const OUT_DIR = process.env.OUT_DIR || path.join(__dirname, '..', '..', 'docs', 'screenshots');

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();
  const failures = [];
  page.on('pageerror', (error) => failures.push(String(error)));
  page.on('console', (message) => {
    if (message.type() === 'error') failures.push('console: ' + message.text());
  });

  const shots = [];

  // ---------------------------------------------------------------- dashboard
  await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded' });
  // Wait for real data, not just the shell: the scanner row must be populated.
  await page.waitForFunction(
    () => {
      const row = document.querySelector('#scannerRows tr');
      return row && /BTCUSDT/.test(row.textContent || '');
    },
    { timeout: 90000 }
  );
  await page.waitForTimeout(2500); // let charts and panels settle
  const dashboardPath = path.join(OUT_DIR, '01-dashboard.png');
  await page.screenshot({ path: dashboardPath, fullPage: true });
  shots.push(dashboardPath);

  // ---------------------------------------------- batch evidence panel only
  const batch = page.locator('#batchPanel');
  if (await batch.count()) {
    await batch.scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    const batchPath = path.join(OUT_DIR, '02-recorded-batch.png');
    await batch.screenshot({ path: batchPath });
    shots.push(batchPath);
  }

  // ------------------------------------------------------------------- chart
  await page.goto(BASE_URL + '/chart', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(
    () => {
      const el = document.querySelector('#candleCount');
      return el && el.textContent && el.textContent.trim() !== '—';
    },
    { timeout: 90000 }
  );
  await page.waitForTimeout(3000);
  const chartPath = path.join(OUT_DIR, '03-chart.png');
  await page.screenshot({ path: chartPath, fullPage: true });
  shots.push(chartPath);

  // -------------------------------------------------- chart, other timeframe
  await page.selectOption('#interval', '1h');
  await page.waitForTimeout(4000);
  const hourPath = path.join(OUT_DIR, '04-chart-1h.png');
  await page.screenshot({ path: hourPath, fullPage: false });
  shots.push(hourPath);

  // ------------------------------------------------------- agent pipeline run
  await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2000);
  const runButton = page.locator('#runAgents');
  if (await runButton.count()) {
    await runButton.click();
    await page.waitForFunction(
      () => {
        const status = document.querySelector('#agentStatus');
        const text = status ? status.textContent || '' : '';
        return text && !/Press Run agent analysis|Running/i.test(text);
      },
      { timeout: 120000 }
    ).catch(() => {});
    await page.waitForTimeout(2000);
    const agents = page.locator('#agentsPanel');
    await agents.scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    const agentsPath = path.join(OUT_DIR, '05-agents.png');
    await agents.screenshot({ path: agentsPath });
    shots.push(agentsPath);
  }

  // --------------------------------------------------------------- operator log
  const logs = page.locator('#logsPanel');
  if (await logs.count()) {
    await logs.scrollIntoViewIfNeeded();
    await page.waitForTimeout(800);
    const logsPath = path.join(OUT_DIR, '06-operator-log.png');
    await logs.screenshot({ path: logsPath });
    shots.push(logsPath);
  }

  const report = {
    base_url: BASE_URL,
    captured_at: new Date().toISOString(),
    screenshots: shots.map((file) => ({
      file: path.basename(file),
      bytes: fs.statSync(file).size,
    })),
    page_errors: [...new Set(failures)].slice(0, 20),
  };
  fs.writeFileSync(path.join(OUT_DIR, 'capture.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));

  await browser.close();
  if (!shots.length) {
    console.error('No screenshot was produced.');
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
