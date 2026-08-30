import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The viewer is a pure client; all data comes from the read-only `ampi serve`
 * API. Proxying /api in dev keeps the app origin-relative so the same build
 * works when served behind anything.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 47811,
    strictPort: true,
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: process.env.AMPI_API ?? "http://127.0.0.1:47913",
        changeOrigin: true,
      },
    },
  },
  preview: { port: 47811, strictPort: true },
  build: { outDir: "dist", sourcemap: true },
});
