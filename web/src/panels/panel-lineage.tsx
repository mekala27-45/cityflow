'use client';

import { useState } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Panel } from '@/components/panel';
import { LineageGraph } from '@/charts/lineage-graph';
import { MetricCatalogBrowser } from '@/charts/metric-catalog';
import type { Metric } from '@/lib/types';

export function PanelLineage() {
  const { catalog, lineage } = useApp();
  const [chosen, setChosen] = useState<Metric | null>(null);

  // The default falls out of the catalog in render rather than being pushed into
  // state by an effect, so the panel is never momentarily empty on arrival and
  // the graph always has a chain to highlight.
  const selected = chosen ?? catalog?.metrics[0] ?? null;

  return (
    <Panel
      id="lineage"
      question="Where does each number come from?"
      intro={
        <>
          Every aggregate on this page is spliced out of the catalog below at runtime rather than written
          again in the dashboard, so what is printed here is the definition the page actually ran, not a
          description of it.
        </>
      }
    >
      <ChartCard
        testId="chart-metric-catalog"
        title="The metric catalog"
        subtitle="Search by name, by definition, or by the column a metric is built from."
        chart={<MetricCatalogBrowser selected={selected} onSelect={setChosen} />}
        table={
          catalog ? (
            <DataTable
              rows={catalog.metrics}
              columns={[
                { key: 'name', label: 'Metric' },
                { key: 'kind', label: 'Kind' },
                { key: 'unit', label: 'Unit' },
                { key: 'interval', label: 'Interval method', render: (m) => m.interval ?? 'none' },
                { key: 'owner', label: 'Owner' },
                { key: 'models', label: 'Models', render: (m) => m.models.join(', ') },
                { key: 'components', label: 'Components', render: (m) => m.components.join(', ') },
                { key: 'filters', label: 'Filters', render: (m) => (m.filters.length > 0 ? m.filters.join('; ') : '') },
              ]}
              caption="Every metric the catalog publishes."
              pageSize={16}
            />
          ) : undefined
        }
        footnote={
          <>
            Sixteen metrics, one owner, one definition each. The two SQL blocks are the same definition
            compiled for two engines: warehouse_sql runs over the fact table in dbt, browser_sql runs over
            whatever aggregate a panel puts in a CTE named agg. The page lifts the projection out of the
            second one and splices it into its queries, which is why a definition change in dbt reaches
            this dashboard without a TypeScript edit.
          </>
        }
      />

      <ChartCard
        testId="chart-lineage-graph"
        title={selected ? `What ${selected.name} is built from` : 'The dbt graph'}
        subtitle="Source on the left, the panels that display the metric on the right. The highlighted chain is the selected metric's."
        chart={<LineageGraph metric={selected} />}
        table={
          lineage ? (
            <DataTable
              rows={[
                ...Object.entries(lineage.nodes).map(([id, node]) => ({
                  id,
                  name: node.name,
                  type: node.type,
                  layer: node.layer,
                  depends_on: node.depends_on.map((d) => d.split('.').pop()).join(', '),
                  description: node.description,
                })),
                ...lineage.exposures.map((exposure) => ({
                  id: `exposure.${exposure.name}`,
                  name: exposure.label,
                  type: 'exposure',
                  layer: 'exposure',
                  depends_on: exposure.depends_on.map((d) => d.split('.').pop()).join(', '),
                  description: exposure.description,
                })),
              ]}
              columns={[
                { key: 'name', label: 'Node' },
                { key: 'type', label: 'Type' },
                { key: 'layer', label: 'Layer' },
                { key: 'depends_on', label: 'Depends on' },
                { key: 'description', label: 'Description' },
              ]}
              caption="Every node in the published dbt manifest, plus the exposures."
              pageSize={12}
            />
          ) : undefined
        }
        footnote={
          <>
            Read left to right: a source table, a staging model that types and renames it, an intermediate
            model that resolves geography, the fact table, and the panel that puts a number on screen. An
            exposure is a dbt object, not a label added here, so the rightmost column is the warehouse&apos;s
            own record of which dashboard depends on which model. The layout is a fixed layered assignment
            rather than a force simulation: the columns are the dbt layers and nothing should be free to
            drift out of its own.
          </>
        }
      />
    </Panel>
  );
}
