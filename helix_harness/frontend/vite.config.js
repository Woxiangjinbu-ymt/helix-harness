import { defineConfig } from "vite";
export default defineConfig({
  build: {
    rollupOptions: { output: { manualChunks: { charts: ["recharts"] } } },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.RSI_API || "http://127.0.0.1:8765",
        changeOrigin: true,
      },
    },
  },
});
