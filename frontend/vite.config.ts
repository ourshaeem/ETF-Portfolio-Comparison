import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API + WebSocket calls to the FastAPI service on :8000.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id) => (id.includes("node_modules") ? (/react-dom|scheduler/.test(id) ? "react" : "vendor") : undefined),
      },
    },
    chunkSizeWarningLimit: 600,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
