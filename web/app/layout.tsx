import type { Metadata, Viewport } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'cityflow',
  description:
    'A browser side dashboard over NYC taxi and for hire vehicle trip aggregates, queried with DuckDB WASM over HTTP range requests.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: '#0B0F14' },
    { media: '(prefers-color-scheme: light)', color: '#FAFAFA' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
