const {
    defineConfig,
    globalIgnores,
} = require("eslint/config");

const globals = require("globals");
const tsParser = require("@typescript-eslint/parser");
const js = require("@eslint/js");
const ts = require("@typescript-eslint/eslint-plugin");
const vue = require("eslint-plugin-vue");
const prettier = require("eslint-config-prettier/flat");

const setupCompilerMacros = {
    defineProps: "readonly",
    defineEmits: "readonly",
    defineExpose: "readonly",
    defineModel: "readonly",
    defineOptions: "readonly",
    defineSlots: "readonly",
    withDefaults: "readonly",
};

module.exports = defineConfig([
    globalIgnores([
        ".eslintrc.cjs",
        "eslint.config.cjs",
        "**/openapi.json",
        "src/api/types.ts",
        "**/*.json",
        "**/*.css",
        "**/*.timestamp-*.mjs",
        ".vite/**",
    ]),
    {
        languageOptions: {
            globals: {
                ...globals.browser,
                ...globals.node,
                ...setupCompilerMacros,
            },
            ecmaVersion: "latest",
            sourceType: "module",
            parserOptions: {
                parser: tsParser,
            },
        },
        plugins: {
            "@typescript-eslint": ts,
            vue,
        },
    },
    js.configs.recommended,
    ...ts.configs["flat/recommended"],
    ...vue.configs["flat/recommended"],
    {
        rules: {
            "@typescript-eslint/no-explicit-any": "warn",
        },
    },
    {
        files: ["*.vue", "**/*.vue"],
        rules: {
            "vue/multi-word-component-names": "off",
            "vue/require-default-prop": "off",
        },
    },
    prettier,
]);
