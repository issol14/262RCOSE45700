import { cp } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { defineConfig } from 'vite';

// pdf.js loads its character maps, fonts, wasm decoders and colour profiles at run time from
// /pdf-assets/; copy them next to the build.
const pdfRoot = path.dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json'));
const pdfAssets = {
  name: 'pdf-assets',
  async closeBundle() {
    for (const folder of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) {
      await cp(path.join(pdfRoot, folder), path.join('dist', 'pdf-assets', folder), { recursive: true });
    }
  },
};

export default defineConfig({
  plugins: [pdfAssets],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/pdf-assets': 'http://127.0.0.1:8000' } },
});
