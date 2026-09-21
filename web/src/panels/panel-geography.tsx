'use client';

import { Panel } from '@/components/panel';
import { OdFlows } from '@/charts/od-flows';
import { ZoneChoropleth } from '@/charts/zone-choropleth';
import { ZoneComparisons } from '@/charts/zone-comparisons';
import { ZonePareto } from '@/charts/zone-pareto';

export function PanelGeography() {
  return (
    <Panel
      id="geography"
      question="Where do trips start and end, and how concentrated is that?"
      intro={
        <>
          Geography is the one place a trip with no recorded location cannot be shown, so it is the one
          place the page has to say what it is leaving out. Every chart here states its exclusion and the
          share it costs.
        </>
      }
    >
      <div className="grid gap-5 xl:grid-cols-2">
        <ZoneChoropleth />
        <OdFlows />
      </div>
      <ZonePareto />
      <ZoneComparisons />
    </Panel>
  );
}
