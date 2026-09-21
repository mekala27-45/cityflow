'use client';

import { Panel } from '@/components/panel';
import { ProvenanceBanner } from '@/components/provenance';
import { NullRates } from '@/charts/null-rates';
import { QuarantineLog } from '@/charts/quarantine-log';
import { SourceFreshness } from '@/charts/source-freshness';

export function PanelTrust() {
  return (
    <Panel
      id="trust"
      question="Can you trust these numbers?"
      intro={
        <>
          The three marts behind this panel are the pipeline auditing itself: what it read, what it
          removed and what was missing from the files it read. They are keyed by source period and
          service, so the date filter applies at month resolution and the day type and borough filters do
          not reach them at all.
        </>
      }
    >
      <ProvenanceBanner testId="provenance-banner-panel" />
      <QuarantineLog />
      <SourceFreshness />
      <NullRates />
    </Panel>
  );
}
