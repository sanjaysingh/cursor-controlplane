import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["tests/client/**/*.test.js"],
    environment: "node",
  },
});
