import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the PredHeat backend so no CORS config is needed locally.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8750" } },
});
