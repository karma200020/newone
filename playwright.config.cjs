const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  testMatch: "browser.spec.cjs",
  workers: 1,
  timeout: 45000,
  use: {
    baseURL: "http://127.0.0.1:8010",
    channel: process.env.PLAYWRIGHT_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "node scripts/browser_server.cjs",
    url: "http://127.0.0.1:8010/health/ready",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
