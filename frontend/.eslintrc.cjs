module.exports = {
  root: true,
  env: { browser: true, es2021: true, node: true, 'vue/setup-compiler-macros': true },
  parser: 'vue-eslint-parser',
  parserOptions: {
    parser: '@typescript-eslint/parser',
    ecmaVersion: 'latest',
    sourceType: 'module',
  },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:vue/vue3-recommended',
    'prettier',
  ],
  rules: {
    '@typescript-eslint/no-explicit-any': 'warn',
    'vue/multi-word-component-names': 'off',
    'vue/require-default-prop': 'off',
  },
  ignorePatterns: ['openapi.json', 'src/api/types.ts', '**/*.json', '**/*.css', '**/*.timestamp-*.mjs']
}
