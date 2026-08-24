import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['src/test/setup.ts'],
      coverage: {
        provider: 'v8',
        include: ['src/**/*.ts', 'src/**/*.vue'],
        exclude: ['src/**/*.spec.ts', 'src/test/**', 'src/api/types.ts'],
      },
    },
  }),
)
