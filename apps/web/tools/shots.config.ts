import base from "../playwright.config";
import { defineConfig } from "@playwright/test";

// 仅用于目检截图；沿用主配置的 webServer 与视口，只把 testDir 指到 tools/。
export default defineConfig({ ...base, testDir: ".", testMatch: /visual-shots\.spec\.ts/, reporter: [["list"]] });
