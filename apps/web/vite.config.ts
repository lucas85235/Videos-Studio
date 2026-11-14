import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const backend = process.env.VS_BACKEND_URL ?? 'http://127.0.0.1:8765';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    fs: { allow: ['..', '../..'] },
    proxy: {
      '/api': { target: backend, changeOrigin: false },
      '/files': { target: backend, changeOrigin: false },
      '/fonts': { target: backend, changeOrigin: false },
      '/textures': { target: backend, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 2000,
  },
});
