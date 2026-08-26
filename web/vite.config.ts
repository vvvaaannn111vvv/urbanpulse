import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev the API is proxied so the browser talks to a single origin and the
// websocket upgrade works without CORS. In production VITE_API_BASE/VITE_WS_URL
// point at the deployed API (see .env.example).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_BASE ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      "/ws": {
        target: process.env.VITE_API_BASE ?? "http://localhost:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      // maplibre and d3 are the two heavy deps; splitting them keeps the app
      // chunk small and lets the browser cache them across deploys.
      output: {
        manualChunks: { maplibre: ["maplibre-gl"], d3: ["d3"] },
      },
    },
  },
});
