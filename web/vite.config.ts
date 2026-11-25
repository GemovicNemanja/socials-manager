import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The FastAPI process (`socialmgr serve`) serves the built SPA from web/dist
// and the REST API under /api. In dev, Vite proxies /api to it
// (override the target with SOCIALMGR_API_URL).
const apiTarget = process.env.SOCIALMGR_API_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": apiTarget,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
