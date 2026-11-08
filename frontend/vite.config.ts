import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three"],
          orbit: ["three/addons/controls/OrbitControls.js"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/projects": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
      "/references": "http://127.0.0.1:8000",
      "/api": "http://127.0.0.1:8000",
    },
  },
});
