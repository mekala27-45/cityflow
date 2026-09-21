// Theme lives outside React because it has three inputs: the stored choice, the
// operating system preference, and the toggle. useSyncExternalStore reads all
// three through one snapshot, which keeps the static export's prerendered markup
// (always dark) from fighting the client's first render.

import type { ThemeName } from './palette';

const KEY = 'cityflow.theme';

const listeners = new Set<() => void>();
let media: MediaQueryList | null = null;
let snapshot: ThemeName = 'dark';
let read = false;

function resolve(): ThemeName {
  try {
    const stored = window.localStorage.getItem(KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    // Storage can be blocked. The preference query still works.
  }
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

function refresh(): void {
  const next = resolve();
  if (next === snapshot) return;
  snapshot = next;
  for (const fn of listeners) fn();
}

export const themeStore = {
  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    if (!media && typeof window !== 'undefined') {
      media = window.matchMedia('(prefers-color-scheme: light)');
      media.addEventListener('change', refresh);
    }
    return () => {
      listeners.delete(fn);
    };
  },

  getSnapshot(): ThemeName {
    if (!read && typeof window !== 'undefined') {
      read = true;
      snapshot = resolve();
    }
    return snapshot;
  },

  getServerSnapshot(): ThemeName {
    return 'dark';
  },

  set(theme: ThemeName): void {
    try {
      window.localStorage.setItem(KEY, theme);
    } catch {
      // Without storage the toggle still works for this page view.
    }
    refresh();
  },
};
