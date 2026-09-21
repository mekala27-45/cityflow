'use client';

import { Panel } from '@/components/panel';
import { DurationRidgeline } from '@/charts/duration-ridgeline';
import { FareDistance } from '@/charts/fare-distance';
import { TipRateMultiples } from '@/charts/tip-rate-multiples';

export function PanelBehavior() {
  return (
    <Panel
      id="behavior"
      question="How do trips differ by hour, and what does that cost the rider?"
      intro={
        <>
          Three of the four marts behind this panel are keyed by service and hour rather than by date, so
          the date and day type filters reach less of it than they do elsewhere. Each chart says which
          filters it answers to.
        </>
      }
    >
      <DurationRidgeline />
      <FareDistance />
      <TipRateMultiples />
    </Panel>
  );
}
