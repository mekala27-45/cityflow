// GitHub Pages serves this project from a subdirectory, so nothing may be fetched
// from an absolute root path. next.config.ts bakes the prefix into the bundle and
// every data, wasm and worker URL is built through here.
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

export function assetUrl(path: string): string {
  const clean = path.startsWith('/') ? path : `/${path}`;
  return `${BASE_PATH}${clean}`;
}

export function dataUrl(file: string): string {
  return assetUrl(`/data/${file}`);
}

/** Absolute URL, which DuckDB's HTTP protocol needs: it cannot resolve relatives. */
export function absoluteDataUrl(file: string): string {
  if (typeof window === 'undefined') return dataUrl(file);
  return new URL(dataUrl(file), window.location.href).toString();
}
