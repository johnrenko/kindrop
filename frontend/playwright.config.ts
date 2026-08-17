import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  reporter: "line",
  use: {
    baseURL: process.env.KINDROP_E2E_BASE_URL ?? "http://127.0.0.1:8787",
    channel: "chrome",
    trace: "retain-on-failure",
  },
});
