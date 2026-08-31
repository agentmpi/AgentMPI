import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies /api to the trace server (scripts/trace_server.py) so the
// viewer can be opened directly without a build step.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 43117,
    strictPort: true,
    proxy: {
      "/api": { target: "http://127.0.0.1:43118", changeOrigin: true },
    },
  },
  build: { outDir: "dist", sourcemap: true },
});
