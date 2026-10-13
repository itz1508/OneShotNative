import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import svgr from 'vite-plugin-svgr'

// Custom plugin to handle ?import&react syntax (alias to ?react)
const svgImportPlugin = () => ({
  name: 'svg-import-alias',
  resolveId(id: string) {
    // Transform ?import&react to ?react for vite-plugin-svgr
    if (id.includes('?import&react')) {
      return id.replace('?import&react', '?react');
    }
    return null;
  },
});

// https://vite.dev/config/
export default defineConfig(() => ({
  root: 'frontend',
  publicDir: '../public',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
  },
  plugins: [
    react(),
    tailwindcss(),
    svgImportPlugin(),
    svgr({
      // Support named ReactComponent export (for ?react syntax)
      svgrOptions: {
        exportType: 'named',
        namedExport: 'ReactComponent',
        ref: true,
        svgo: false,
        titleProp: true,
      },
      include: '**/*.svg?react',
    }),
  ],
  server: {
    allowedHosts: true as const,
    hmr: false,
    // The OneShot System is a separate Python service that runs alongside this
    // dev server on 127.0.0.1:8000. Proxying it here keeps the browser on a
    // single origin: nothing to configure, no CORS to get right, and the live
    // Process Display (Server-Sent Events) streams straight through. A System
    // hosted somewhere else is still reachable by setting an absolute URL in the
    // header — see src/lib/config.ts.
    proxy: {
      '/oneshot': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // The System's routes live at its root, so the proxy prefix is stripped.
        rewrite: (path) => path.replace(/^\/oneshot/, ''),
        // SSE frames must reach the browser as they are produced, never buffered
        // or transformed in between.
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            const contentType = proxyRes.headers['content-type'] ?? '';
            if (String(contentType).includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache, no-transform';
              proxyRes.headers['x-accel-buffering'] = 'no';
            }
          });
        },
      },
    },
  },
}))