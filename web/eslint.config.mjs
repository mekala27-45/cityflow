import coreWebVitals from 'eslint-config-next/core-web-vitals';
import typescriptRules from 'eslint-config-next/typescript';

// eslint-config-next 16 ships flat configs, so FlatCompat is not needed and in
// fact breaks on the plugin graph it exports.
const config = [
  // public holds vendored runtime files copied out of node_modules by
  // scripts/prepare-assets.mjs. They are not this project's source.
  { ignores: ['out/**', '.next/**', 'node_modules/**', 'public/**', 'next-env.d.ts', 'tests/**'] },
  ...coreWebVitals,
  ...typescriptRules,
  {
    rules: {
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
];

export default config;
