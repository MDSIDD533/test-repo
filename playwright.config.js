// Browser verification config. Requires `npm install --no-save @playwright/test`
// and `npx playwright install --with-deps chromium`, which is what the
// live-verification workflow does. The app must already be running.
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/browser',
  timeout: 120000,
  expect: { timeout: 60000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['line']],
  outputDir: './evidence/playwright-output',
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8000',
    headless: true,
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: {
      // Binance and the local app are both reached over the network.
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
  },
});
