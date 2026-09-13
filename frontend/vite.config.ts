import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
import { cpSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'

const require = createRequire(import.meta.url)

const allowedHosts = process.env.VITE_ALLOWED_HOSTS
  ? process.env.VITE_ALLOWED_HOSTS.split(',').map((h) => h.trim()).filter(Boolean)
  : []

// Replaces the Petstore demo URL in the stock swagger-initializer.js.
const SWAGGER_INITIALIZER = `window.onload = function() {
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: '#swagger-ui',
    deepLinking: true,
    persistAuthorization: true,
    presets: [
      SwaggerUIBundle.presets.apis,
      SwaggerUIStandalonePreset
    ],
    plugins: [
      SwaggerUIBundle.plugins.DownloadUrl
    ],
    layout: "StandaloneLayout"
  });
};
`

// Copies the swagger-ui-dist bundle into the build output so FastAPI can
// serve it at /swagger-ui/ without a separate container.
function swaggerUi(): Plugin {
  let outDir = ''
  return {
    name: 'songhive-swagger-ui',
    apply: 'build',
    configResolved(config) {
      outDir = config.build.outDir
    },
    closeBundle() {
      const distDir = dirname(require.resolve('swagger-ui-dist/package.json'))
      const target = resolve(outDir, 'swagger-ui')
      cpSync(distDir, target, { recursive: true })
      writeFileSync(join(target, 'swagger-initializer.js'), SWAGGER_INITIALIZER)
    },
  }
}

export default defineConfig({
  plugins: [vue(), swaggerUi()],
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src'),
    },
  },
  server: {
    host: true,
    allowedHosts,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        autoRewrite: true,
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
      '/manifest.webmanifest': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // Swagger UI and the OpenAPI document are served by the backend; the
      // bundled assets exist in songhive/static after a build.
      '/swagger-ui': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/openapi.json': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../songhive/static',
    emptyOutDir: true,
  },
})
