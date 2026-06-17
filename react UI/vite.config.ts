import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxyTarget = (env.VITE_PROXY_TARGET || "http://127.0.0.1:8000").trim();

  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": {
          target: proxyTarget,
          changeOrigin: true,
          secure: false,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    esbuild: {
      logOverride: {
        "ignored-directive": "silent",
      },
    },
    logLevel: "info",
    build: {
      rollupOptions: {
        onwarn(warning, warn) {
          if (
            warning.message.includes("Module level directives") ||
            warning.message.includes('"use client"') ||
            warning.message.includes('"was ignored"')
          ) {
            return;
          }
          if (warning.code === "UNRESOLVED_IMPORT") {
            throw new Error(`Build failed due to unresolved import:\n${warning.message}`);
          }
          if (warning.code === "PLUGIN_WARNING" && /is not exported/.test(warning.message)) {
            throw new Error(`Build failed due to missing export:\n${warning.message}`);
          }
          warn(warning);
        },
      },
    },
  };
});
