import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';

// Diagnostics serves this directory at /map-viewer/. One ES module, no demo data copy.
export default defineConfig({
  publicDir: false,
  define: {'process.env.NODE_ENV': JSON.stringify('production')},
  plugins: [react()],
  build: {
    outDir: 'dist-embed',
    emptyOutDir: true,
    cssCodeSplit: false,
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    lib: {
      entry: fileURLToPath(new URL('./src/embed.tsx', import.meta.url)),
      formats: ['es'],
      fileName: 'visualization',
    },
    rollupOptions: {
      external: [],
      output: {
        inlineDynamicImports: true,
        entryFileNames: 'visualization.js',
      },
    },
  },
});
