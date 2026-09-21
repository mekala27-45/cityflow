'use client';

import { useApp } from './app-context';
import { compactCount, monthLabel } from '@/lib/format';

export const REPO_PROVENANCE_URL = 'https://github.com/mekala27-45/cityflow#data-provenance';

/**
 * manifest.json decides what this says. The backend and the window are read from
 * the same file the written documents are rendered from, so the banner and the
 * prose cannot disagree about what was published: if the pipeline is ever pointed
 * at the real TLC files, both change together and neither needs editing.
 */
export function ProvenanceBanner({ testId = 'provenance-banner' }: { testId?: string } = {}) {
  const { bootstrap, manifest } = useApp();
  const provenance = bootstrap?.provenance;
  const backends = manifest?.backend ?? [];
  const backend = backends.length > 0 ? backends.join(' and ') : (provenance?.backend ?? null);
  const synthetic = backends.length > 0 ? backends.every((b) => b === 'synthetic') : backend === 'synthetic';
  const window = manifest?.window ?? null;

  if (!provenance && !manifest) {
    return (
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        data-testid={testId}
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        Reading source provenance from mart_source_freshness.
      </div>
    );
  }

  const claims = manifest?.claims ?? {};

  return (
    <div
      data-testid={testId}
      data-backend={backends[0] ?? backend ?? 'unknown'}
      className="rounded-lg border px-4 py-3"
      style={{
        borderColor: synthetic ? 'var(--status-warn)' : 'var(--border)',
        background: 'var(--panel)',
      }}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span
          className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
          style={{
            background: synthetic ? 'var(--status-warn)' : 'var(--status-ok)',
            color: 'var(--surface)',
          }}
        >
          Source: {backend ?? 'unknown'}
        </span>
        <p className="text-sm leading-relaxed" style={{ color: 'var(--text)' }}>
          {synthetic ? (
            <>
              Every figure on this page is measured on a seeded generator that reproduces the TLC trip
              record schema and its known defects. It is not measured on the published TLC files.
              The pipeline, the metric definitions and the statistics are real; the trips are not.{' '}
              <a
                href={REPO_PROVENANCE_URL}
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2"
                style={{ color: 'var(--focus)' }}
              >
                How the generator works
              </a>
              .
            </>
          ) : (
            <>
              Figures on this page are measured on source marked{' '}
              <code style={{ color: 'var(--text)' }}>{backend}</code> in mart_source_freshness.
            </>
          )}
        </p>
      </div>
      <p className="mt-2 text-xs" style={{ color: 'var(--text-faint)' }}>
        {claims.months ?? provenance?.periods} source periods
        {window ? `, ${monthLabel(window.start)} to ${monthLabel(window.end)}` : ''}.{' '}
        {compactCount(Number(claims.source_rows ?? provenance?.source_rows ?? 0))} rows read,{' '}
        {compactCount(Number(claims.clean_rows ?? provenance?.clean_rows ?? 0))} kept,{' '}
        {compactCount(Number(claims.quarantined_rows ?? provenance?.quarantined_rows ?? 0))} quarantined
        {claims.vintages ? `, across ${claims.vintages} schema vintages` : ''}.{' '}
        {provenance && provenance.gaps > 0
          ? `${provenance.gaps} periods follow a gap.`
          : 'No month is missing a predecessor.'}{' '}
        {manifest ? `Built from commit ${manifest.commit}.` : ''}
      </p>
    </div>
  );
}

/** Compact restatement for the page header, so the caveat rides along on scroll. */
export function ProvenancePill() {
  const { bootstrap, manifest } = useApp();
  const backends = manifest?.backend ?? [];
  const backend = backends.length > 0 ? backends.join(' and ') : bootstrap?.provenance.backend;
  if (!backend) return null;
  const synthetic = backends.length > 0 ? backends.every((b) => b === 'synthetic') : backend === 'synthetic';
  return (
    <a
      href={synthetic ? REPO_PROVENANCE_URL : '#trust'}
      target={synthetic ? '_blank' : undefined}
      rel={synthetic ? 'noreferrer' : undefined}
      data-testid="provenance-pill"
      className="flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px]"
      style={{ borderColor: synthetic ? 'var(--status-warn)' : 'var(--border)', color: 'var(--text-muted)' }}
      title={
        synthetic
          ? 'Figures are measured on a seeded generator, not on the published TLC files.'
          : `Source backend: ${backend}`
      }
    >
      <span
        aria-hidden
        style={{
          width: 7,
          height: 7,
          borderRadius: 999,
          background: synthetic ? 'var(--status-warn)' : 'var(--status-ok)',
        }}
      />
      {synthetic ? 'Synthetic source, not the published TLC files' : `Source: ${backend}`}
    </a>
  );
}
