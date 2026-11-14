import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// The admin API; `uvicorn botchan_api.admin.main:app --port 8001` listens here.
const API = "http://localhost:8001";

export default defineConfig({
    plugins: [
        vue({
            // Leave absolute URLs such as /botchan-192.png alone: the API
            // serves them, so they are not part of the build.
            template: { transformAssetUrls: { includeAbsolute: false } },
        }),
    ],
    build: { outDir: "dist", emptyOutDir: true },
    server: {
        proxy: {
            "/api": API,
            "/auth": API,
            "/botchan-192.png": API,
        },
    },
});
