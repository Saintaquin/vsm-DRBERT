import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Backend FastAPI local (127.0.0.1:8741) — proxy en dev uniquement.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    proxy: {
      "/auth": "http://127.0.0.1:8741",
      "/documents": "http://127.0.0.1:8741",
      "/vsm": "http://127.0.0.1:8741",
      "/audit": "http://127.0.0.1:8741",
      "/health": "http://127.0.0.1:8741",
    },
  },
  build: { outDir: "dist", sourcemap: false },
});
