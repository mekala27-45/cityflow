// One palette, two surfaces. The hue for an entity is fixed by the entity, never
// by its rank in the current filter, so a filter that drops green does not repaint
// yellow. That is the whole reason SERIES_BY_ENTITY exists instead of an index.

export type ThemeName = 'dark' | 'light';

export const SURFACE: Record<ThemeName, string> = {
  dark: '#0B0F14',
  light: '#FAFAFA',
};

/**
 * Eight categorical hues per surface, validated by scripts/validate_palette.js.
 * Slot 6 is #2563EB on dark and #1D4ED8 on light rather than a lighter blue:
 * the earlier value sat within 0.014 lightness and 0.020 chroma of slot 5's red,
 * under the validator's floors, which is the pair a reader would have had to
 * separate by hue alone.
 */
export const SERIES: Record<ThemeName, readonly string[]> = {
  dark: ['#0891B2', '#D97706', '#8B5CF6', '#059669', '#EF4444', '#2563EB', '#EC4899', '#65A30D'],
  light: ['#0891B2', '#B45309', '#7C3AED', '#047857', '#DC2626', '#1D4ED8', '#DB2777', '#4D7C0F'],
};

/**
 * Single hue cyan ramp, darkest first. Magnitude never gets a rainbow.
 *
 * The two surfaces get different six step ramps rather than one ramp read in
 * two directions. On white the dark ramp's first two steps were 0.052 apart in
 * lightness, under the 0.06 ordinal floor, so the light ramp starts a step
 * deeper at #083344 and drops the lightest step, which had nowhere to sit
 * against the surface anyway.
 */
export const SEQUENTIAL_BY_THEME: Record<ThemeName, readonly string[]> = {
  dark: ['#155E75', '#0E7490', '#0891B2', '#06B6D4', '#22D3EE', '#67E8F9'],
  light: ['#083344', '#155E75', '#0E7490', '#0891B2', '#06B6D4', '#22D3EE'],
};

/** The dark ramp, for anything that needs the canonical six. */
export const SEQUENTIAL = SEQUENTIAL_BY_THEME.dark;

/**
 * The ramp ordered low value to high value for the surface in use. On the dark
 * surface a high value is the lighter one, because light against dark is what
 * advances; on the light surface that reverses, and running the ramp the other
 * way round would make an empty cell the loudest thing on the chart.
 */
export function sequentialRamp(theme: ThemeName): string[] {
  const ramp = SEQUENTIAL_BY_THEME[theme];
  return theme === 'light' ? [...ramp].reverse() : [...ramp];
}

/**
 * Cyan cool pole, amber warm pole, neutral midpoint, validated one arm at a time
 * from the midpoint outwards. The midpoint is deliberately below the contrast
 * floor against its surface: a diverging scale's zero is meant to recede, and
 * checking it as if it were a data colour tests the wrong thing.
 *
 * Defined, and not yet used: no chart on this page encodes a signed magnitude.
 * It is here so that the first one does not have to invent a scale.
 */
export const DIVERGING: Record<ThemeName, readonly string[]> = {
  dark: ['#67E8F9', '#22D3EE', '#0891B2', '#383835', '#D97706', '#B45309', '#92400E'],
  light: ['#155E75', '#0E7490', '#0891B2', '#F0EFEC', '#D97706', '#B45309', '#92400E'],
};

// Series index by entity, one based to match the token names in the brief.
const SERIES_BY_ENTITY: Record<string, number> = {
  fhvhv: 1,
  yellow: 2,
  purple: 3,
  green: 4,
};

export const SERVICE_ORDER = ['fhvhv', 'yellow', 'green'] as const;
export type ServiceId = (typeof SERVICE_ORDER)[number];

export const SERVICE_LABEL: Record<string, string> = {
  fhvhv: 'For hire vehicle',
  yellow: 'Yellow taxi',
  green: 'Green taxi',
};

/** For control groups inside a card header, where the full label does not fit
 *  on a phone and the card's own subtitle already names the service. */
export const SERVICE_SHORT: Record<string, string> = {
  fhvhv: 'FHV',
  yellow: 'Yellow',
  green: 'Green',
};

export function seriesColor(theme: ThemeName, slot: number): string {
  const ramp = SERIES[theme];
  const index = Math.max(0, Math.min(ramp.length - 1, slot - 1));
  return ramp[index] ?? ramp[0]!;
}

export function serviceColor(theme: ThemeName, service: string): string {
  const slot = SERIES_BY_ENTITY[service];
  if (slot) return seriesColor(theme, slot);
  // Anything not a known service folds into the neutral slot rather than
  // stealing a reserved hue.
  return seriesColor(theme, 6);
}

/** Colour range for a Plot ordinal scale, ordered to match the domain given. */
export function serviceRange(theme: ThemeName, domain: readonly string[]): string[] {
  return domain.map((s) => serviceColor(theme, s));
}

// Status is state, never a series. Keeping it out of SERIES means a chart can
// never accidentally colour a category with the failure red.
export const STATUS: Record<ThemeName, { ok: string; warn: string; bad: string }> = {
  dark: { ok: '#10B981', warn: '#F59E0B', bad: '#F87171' },
  light: { ok: '#047857', warn: '#B45309', bad: '#B91C1C' },
};

export const INK: Record<ThemeName, { text: string; muted: string; faint: string; grid: string; axis: string; panel: string; border: string }> = {
  dark: {
    text: '#E8EAED',
    muted: '#9AA4AF',
    faint: '#6B7683',
    grid: '#1C242E',
    axis: '#2A3441',
    panel: '#11161D',
    border: '#1F2933',
  },
  light: {
    text: '#1A1F26',
    muted: '#525C66',
    faint: '#7C8794',
    grid: '#E4E4E1',
    axis: '#CFCFCB',
    panel: '#FFFFFF',
    border: '#E2E2DE',
  },
};

function channels(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

/**
 * The sequential ramp read as a continuous scale rather than six steps. Six
 * discrete colours across twenty four hours bands the ridgeline into six blocks,
 * which reads as a category where none exists.
 */
export function sequentialAt(theme: ThemeName, t: number): string {
  const ramp = sequentialRamp(theme);
  const clamped = Number.isFinite(t) ? Math.max(0, Math.min(1, t)) : 0;
  const position = clamped * (ramp.length - 1);
  const low = Math.floor(position);
  const high = Math.min(ramp.length - 1, low + 1);
  const mix = position - low;
  const a = channels(ramp[low] ?? ramp[0]!);
  const b = channels(ramp[high] ?? ramp[0]!);
  const blend = a.map((channel, i) => Math.round(channel + (b[i]! - channel) * mix));
  return `rgb(${blend[0]}, ${blend[1]}, ${blend[2]})`;
}
