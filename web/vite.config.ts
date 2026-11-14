import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// The API this editor talks to; `uvicorn botchan_api.main:app` listens here.
const API = "http://localhost:8000";

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
