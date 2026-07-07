import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend dev server proxies /api to the FastAPI backend.
// Override the target with VITE_API_TARGET (defaults to :8000).
// Use 127.0.0.1 (not "localhost"): on macOS "localhost" resolves to IPv6 ::1
// first, which can land on an unrelated container bound to [::]:8000.
const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
});
