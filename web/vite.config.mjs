import { execSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/** The commit this UI was built from: the app compares it with the server's and says when the build is stale. */
const here = fileURLToPath(new URL(".", import.meta.url));
const rev = (() => {
  try {
    const head = execSync("git rev-parse --short HEAD", { cwd: resolve(here, ".."), stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
    const dirty = execSync("git status --porcelain -- web/src", { cwd: resolve(here, ".."), stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
    return dirty ? `${head}+local` : head;
  } catch {
    return "unknown";
  }
})();

/** @returns {import("vite").Plugin} */
const buildInfo = () => ({
  name: "mf-build-info",
  closeBundle() {
    writeFileSync(resolve(here, "dist", "build.json"), JSON.stringify({ rev, built_at: new Date().toISOString() }));
  },
});

export default defineConfig({
  plugins: [react(), buildInfo()],
  define: { __MF_BUILD_REV__: JSON.stringify(rev) },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8765" } },
  build: {
    outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 500,
    // libraries in their own files: they change rarely, so a UI update does not re-download them
    rollupOptions: { output: { manualChunks: { react: ["react", "react-dom", "react-router-dom"], query: ["@tanstack/react-query"], charts: ["uplot"], icons: ["lucide-react"] } } },
  },
});
