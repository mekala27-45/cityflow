'use client';

interface SparklineProps {
  values: readonly number[];
  color: string;
  width?: number;
  height?: number;
  label: string;
}

/**
 * Ninety points at twelve pixels tall. Hand rolled rather than a Plot instance
 * because five of these mount on first paint and a full scale pipeline per tile
 * is measurable work for a shape that carries no axis.
 */
export function Sparkline({ values, color, width = 104, height = 26, label }: SparklineProps) {
  const clean = values.filter((v) => Number.isFinite(v));
  if (clean.length < 2) {
    return <div style={{ width, height }} aria-hidden />;
  }
  const min = Math.min(...clean);
  const max = Math.max(...clean);
  const span = max - min || 1;
  const step = width / (clean.length - 1);
  const y = (v: number) => height - 2 - ((v - min) / span) * (height - 4);

  const line = clean.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  const area = `${line} L${width},${height} L0,${height} Z`;
  const lastValue = clean[clean.length - 1]!;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      style={{ overflow: 'visible' }}
    >
      <path d={area} fill={color} opacity={0.14} />
      <path d={line} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={width} cy={y(lastValue)} r={2.5} fill={color} />
    </svg>
  );
}
