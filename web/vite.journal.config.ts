import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";

// The public journal: a static site, separate from the owner's dashboard bundle. `base: "./"` keeps
// every asset URL relative, so the build works from any GitHub Pages sub-path and from the local
// preview at /journal/ (`socialmgr serve`). The entry is journal.html; it is emitted as index.html
// so a static host (and the preview mount) serves it for the folder. In dev
// (`pnpm dev:journal`, then open /journal.html), /api is proxied to the local server for the
// preview data.
const apiTarget = process.env.SOCIALMGR_API_URL ?? "http://127.0.0.1:8765";

function emitAsIndex(): Plugin {
  return {
    name: "journal-emit-as-index",
    enforce: "post",
    generateBundle(_options, bundle) {
      const page = bundle["journal.html"];
      if (page) page.fileName = "index.html";
    },
  };
}

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), emitAsIndex()],
  server: {
    proxy: {
      "/api": apiTarget,
    },
  },
  build: {
    outDir: "dist-journal",
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(import.meta.dirname, "journal.html"),
    },
  },
});
