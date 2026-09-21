'use client';

import { Panel } from '@/components/panel';
import { BoroughSankey } from '@/charts/borough-sankey';
import { DurationBoxes } from '@/charts/duration-boxes';
import { IndexedServices } from '@/charts/indexed-services';
import { MarketShare } from '@/charts/market-share';

export function PanelMix() {
  return (
    <Panel
      id="mix"
      question="How have yellow, green and for hire vehicles traded share?"
      intro={
        <>
          For hire vehicles run roughly seven times the volume of yellow and three hundred times that of
          green, so the first two charts answer the question twice: once as relative movement against a
          common base, once as a share of the month. Neither is a second axis on the other.
        </>
      }
    >
      <div className="grid gap-5 xl:grid-cols-2">
        <IndexedServices />
        <MarketShare />
      </div>
      <DurationBoxes />
      <BoroughSankey />
    </Panel>
  );
}
