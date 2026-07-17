import { defineConfig } from "vitest/config";

// Unit tests run in a plain Node environment — the covered logic (unit conversion,
// pure helpers) touches no DOM. Add a jsdom/happy-dom environment later if/when
// component tests are introduced.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
