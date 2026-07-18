import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend dev server proxies /api to the FastAPI backend, which runs on :8010.
// Override the target with VITE_API_TARGET.
// Use 127.0.0.1 (not "localhost"): on macOS "localhost" resolves to IPv6 ::1
// first, which can land on an unrelated container. Default is :8010, NOT :8000 —
// a Docker stack squats :8010's old neighbour :8000 on this machine and returns
// 401 to everything, which makes the UI look like a dead backend.
const apiTarget = process.env.VITE_API_TARGET || "http://127.0.0.1:8010";

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
