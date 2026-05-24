// @ts-nocheck
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(process.cwd());
const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
const viteConfig = readFileSync(resolve(root, "vite.config.ts"), "utf8");
const vitestConfig = readFileSync(resolve(root, "vitest.config.ts"), "utf8");

describe("frontend build config", () => {
  it("keeps the expected package scripts available", () => {
    expect(packageJson.scripts).toMatchObject({
      dev: "vite",
      build: "tsc && vite build",
      typecheck: "tsc --noEmit",
      test: "vitest run",
      preview: "vite preview",
    });
  });

  it("keeps vite and vitest configured for the current frontend workflow", () => {
    expect(viteConfig).toContain("plugin-react");
    expect(viteConfig).toContain("react()");
    expect(vitestConfig).toContain('environment: "jsdom"');
    expect(vitestConfig).toContain("include: [\"src/**/*.test.ts\", \"src/**/*.test.tsx\"]");
  });
});
