import { defineConfig } from "@playwright/test";
import shared from "./playwright.config";

// Separate reports/build/port; all tests intercept /api/v2 and never use 8010.
export default defineConfig(shared, {
  testMatch: /(?:resource-management|navigation-external-links|knowledge-insights|knowledge-growth)\.spec\.ts/,
  outputDir: "e2e/results/agent1/artifacts",
  reporter: [["list"], ["json", { outputFile: "e2e/results/agent1/results.json" }]],
});
