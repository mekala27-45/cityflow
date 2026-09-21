'use client';

export interface LegendItem {
  label: string;
  color: string;
  /** A dashed swatch reads as a modelled or derived layer, not a measured one. */
  dashed?: boolean;
}

/**
 * Shown whenever a chart carries two or more series. Charts with four or fewer
 * also direct label inside the plot, so identity never rests on colour alone.
 */
export function Legend({ items }: { items: readonly LegendItem[] }) {
  if (items.length < 2) return null;
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style={{ color: 'var(--text-muted)' }}>
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            style={{
              display: 'inline-block',
              width: 14,
              height: 2,
              borderRadius: 1,
              background: item.dashed
                ? `repeating-linear-gradient(90deg, ${item.color} 0 4px, transparent 4px 7px)`
                : item.color,
            }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/** Swatch strip for a sequential scale, with the two ends labelled. */
export function RampLegend({
  colors,
  low,
  high,
  caption,
}: {
  colors: readonly string[];
  low: string;
  high: string;
  caption?: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
      {caption ? <span>{caption}</span> : null}
      <span>{low}</span>
      <span className="flex" aria-hidden>
        {colors.map((color) => (
          <span key={color} style={{ background: color, width: 20, height: 10, display: 'inline-block' }} />
        ))}
      </span>
      <span>{high}</span>
    </div>
  );
}
