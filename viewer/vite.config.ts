import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    // An uncommon port, chosen to avoid colliding with anything else the
    // developer is likely to be running.
    port: 43917,
    strictPort: true,
  },
});
