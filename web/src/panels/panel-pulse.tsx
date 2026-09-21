'use client';

import { useApp } from '@/components/app-context';
import { Panel } from '@/components/panel';
import { DailyVolume } from '@/charts/daily-volume';
import { HourOfWeek } from '@/charts/hour-of-week';
import { KpiRow } from '@/charts/kpi-row';

export function PanelPulse() {
  const { engineReady, engineError } = useApp();
  return (
    <Panel
      id="pulse"
      question="What does a normal week look like, and when did that change?"
      intro={
        <>
          The tiles and the two charts read the same window from the filter row above. Until the engine
          finishes loading they run on a small precomputed summary of the default window, which is why a
          number can move once without a filter changing.{' '}
          {engineError ? (
            <strong style={{ color: 'var(--status-bad)' }}>The engine failed to start: {engineError}</strong>
          ) : engineReady ? (
            <span style={{ color: 'var(--text-faint)' }}>Engine ready, figures are live.</span>
          ) : (
            <span style={{ color: 'var(--text-faint)' }}>Engine still loading.</span>
          )}
        </>
      }
    >
      <KpiRow />
      <DailyVolume />
      <HourOfWeek />
    </Panel>
  );
}
