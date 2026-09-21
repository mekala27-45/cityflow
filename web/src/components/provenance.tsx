'use client';

import { useApp } from './app-context';
import { compactCount } from '@/lib/format';

// The section this points at lives in web/README.md, which ships with the code
// that draws the banner, so the link and the explanation move together.
export const REPO_PROVENANCE_URL =
  'https://github.com/mekala27-45/cityflow/blob/main/web/README.md#data-provenance';

/**
 * The backend column of mart_source_freshness decides what this says. It is read,
 * not assumed: if the pipeline is ever pointed at the published TLC files the
 * banner changes on its own. Until then the page has to be plain about the fact
 * that every figure on it is measured on a generator.
 */
export function ProvenanceBanner({ testId = 'provenance-banner' }: { testId?: string } = {}) {
  const { bootstrap } = useApp();
  const provenance = bootstrap?.provenance;
  const backend = provenance?.backend ?? null;
  const synthetic = backend === 'synthetic';

  if (!provenance) {
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

  return (
    <div
      data-testid={testId}
      data-backend={backend ?? 'unknown'}
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
        {provenance.periods} source periods, {provenance.min_period?.slice(0, 7)} to{' '}
        {provenance.max_period?.slice(0, 7)}. {compactCount(provenance.source_rows)} rows read,{' '}
        {compactCount(provenance.clean_rows)} kept, {compactCount(provenance.quarantined_rows)} quarantined.{' '}
        {provenance.gaps === 0 ? 'No month is missing a predecessor.' : `${provenance.gaps} periods follow a gap.`}
      </p>
    </div>
  );
}

/** Compact restatement for the page header, so the caveat rides along on scroll. */
export function ProvenancePill() {
  const { bootstrap } = useApp();
  const backend = bootstrap?.provenance.backend;
  if (!backend) return null;
  const synthetic = backend === 'synthetic';
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
