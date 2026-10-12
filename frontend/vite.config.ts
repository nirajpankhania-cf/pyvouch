import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 8742 sits next to the API's 8741 and avoids the usual 3000/5173/8080 collisions.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 8742,
    strictPort: true,
    // The real API routes, unrewritten, so the client uses the same relative
    // paths in dev and in a built image served by FastAPI itself.
    proxy: {
      "/health": "http://127.0.0.1:8741",
      "/reviews": "http://127.0.0.1:8741",
      "/openapi.json": "http://127.0.0.1:8741",
    },
  },
});
