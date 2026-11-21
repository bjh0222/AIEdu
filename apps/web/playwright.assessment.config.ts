import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Fixture-only smoke against an independently prebuilt tree. Never bind or
// rebuild the user's 3001/.next instance as a side effect of a unit regression.
const port = process.env.AIEDU_ASSESSMENT_TEST_PORT ?? "3105";
const dist = process.env.AIEDU_NEXT_DIST_DIR;
if (!/^\d+$/.test(port) || port === "3001" || !dist || dist === ".next" || !/^\.next-[a-z0-9-]+$/i.test(dist)) {
  throw new Error("Assessment fixture requires an explicit isolated build directory and non-3001 port");
}
const fixtureBase = { ...base };
delete fixtureBase.webServer; // Playwright concatenates webServer entries across configs.
export default defineConfig(fixtureBase, {
  testMatch: "quality-delivery.spec.ts",
  outputDir: process.env.AIEDU_E2E_OUTPUT_DIR ?? "test-results/assessment",
  reporter: [["list"], ["json", { outputFile: "e2e/results/assessment-main03.json" }]],
  webServer: {
    command: `"${process.execPath}" node_modules/next/dist/bin/next start -p ${port} --hostname 127.0.0.1`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { AIEDU_NEXT_DIST_DIR: dist },
  },
  use: { baseURL: `http://127.0.0.1:${port}` },
});
