import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next*/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // e2e/results 下是各轮测试生成的构建产物与报告，不参与 lint。
    "e2e/results/**",
  ]),
]);

export default eslintConfig;
