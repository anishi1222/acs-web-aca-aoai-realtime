import { defineConfig } from "vite";

// Local dev convenience:
// - Web:  http://localhost:5173
// - API:  http://localhost:8000
// This proxy lets the UI call /api/* without hard-coding the server URL.
export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
