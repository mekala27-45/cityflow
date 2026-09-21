import type { NextConfig } from 'next';

// GitHub Pages serves the project at /cityflow, so every asset and every data
// URL has to carry that prefix. basePath handles the framework's own assets;
// runtime data fetches go through src/lib/base-path.ts, which reads the same
// value from an env var baked in below.
const BASE_PATH = '/cityflow';

const nextConfig: NextConfig = {
  output: 'export',
  basePath: BASE_PATH,
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  env: { NEXT_PUBLIC_BASE_PATH: BASE_PATH },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
