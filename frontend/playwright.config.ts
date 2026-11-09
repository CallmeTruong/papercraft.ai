import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 60000,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:8001", headless: true },
  webServer: {
    command:
      "uv run --no-sync papercraft-ai serve --port 8001 --storage .papercraft/test-browser",
    cwd: "..",
    url: "http://127.0.0.1:8001",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
