/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind IPv4 explicitly: Node's default "localhost" host can resolve to
    // the IPv6 loopback only on some systems, which `curl`/browsers reach
    // fine via "localhost" but tools that probe `127.0.0.1` directly (e.g.
    // Playwright's `webServer.url` readiness check) can't.
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      // The FastAPI server (`uv run owlsperch serve`) is mounted at the
      // bare paths (`/search`, `/records/...`, `/schemas`, `/health`,
      // `/stats`); the frontend always calls `/api/*` so a production build
      // could sit behind a reverse proxy using the same convention. Dev
      // mode reproduces that here: strip the `/api` prefix before
      // forwarding to the API server on 127.0.0.1:8000.
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
    exclude: ["e2e/**", "node_modules/**"],
  },
});
