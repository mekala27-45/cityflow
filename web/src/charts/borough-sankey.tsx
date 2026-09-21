'use client';

import { sankey, sankeyLinkHorizontal, type SankeyGraph } from 'd3-sankey';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { Legend } from '@/components/legend';
import { useQuery } from '@/hooks/use-query';
import { compactCount, percent } from '@/lib/format';
import { seriesColor } from '@/lib/palette';
import { boroughPredicate, monthWindow, servicePredicate, type Filters } from '@/lib/sql';
import type { MetricCatalog } from '@/lib/metrics';

interface Row {
  pu_borough: string;
  do_borough: string;
  trips: number;
}

// Hue by borough, fixed. Manhattan is always slot one whether or not it is the
// heaviest in the current filter, which is the point: a filter that drops a
// borough must not repaint the rest.
const BOROUGH_SLOT: Record<string, number> = {
  Manhattan: 1,
  Brooklyn: 2,
  Queens: 3,
  Bronx: 4,
  'Staten Island': 5,
  EWR: 6,
};

interface Node {
  name: string;
  borough: string;
  side: 'from' | 'to';
  x0?: number;
  x1?: number;
  y0?: number;
  y1?: number;
  value?: number;
}

interface Link {
  source: number;
  target: number;
  value: number;
  from: string;
  to: string;
  width?: number;
}

function buildSql(catalog: MetricCatalog, filters: Filters): string {
  return `with agg as (
  select f.trips, pz.borough as pu_borough, dz.borough as do_borough
  from 'agg_od_flow.parquet' f
  join 'dim_zone.parquet' pz on pz.zone_id = f.pu_zone_id
  join 'dim_zone.parquet' dz on dz.zone_id = f.do_zone_id
  where ${monthWindow(filters, 'f.month')}
    and ${servicePredicate(filters, 'f.service')}
    and not pz.is_unknown and not dz.is_unknown
    and ${boroughPredicate(filters, 'pz.borough')}
)
select pu_borough, do_borough, ${catalog.projection('trips')}
from agg
group by 1, 2
order by trips desc`;
}

export function BoroughSankey() {
  const { catalog, filters, engineReady, theme } = useApp();
  const host = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hovered, setHovered] = useState<string | null>(null);

  const sql = useMemo(
    () => (catalog && engineReady ? buildSql(catalog, filters) : null),
    [catalog, filters, engineReady],
  );
  const query = useQuery<Row>(sql, 'borough to borough flows');
  const rows = useMemo(() => query.rows ?? [], [query.rows]);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const next = Math.round(entry.contentRect.width);
      setWidth((prev) => (Math.abs(prev - next) > 2 ? next : prev));
    });
    observer.observe(node);
    setWidth(Math.round(node.getBoundingClientRect().width));
    return () => observer.disconnect();
  }, []);

  const total = rows.reduce((acc, r) => acc + Number(r.trips), 0);

  const graph = useMemo(() => {
    if (rows.length === 0 || width <= 0) return null;
    const origins = [...new Set(rows.map((r) => String(r.pu_borough)))];
    const destinations = [...new Set(rows.map((r) => String(r.do_borough)))];
    const nodes: Node[] = [
      ...origins.map((b) => ({ name: `from:${b}`, borough: b, side: 'from' as const })),
      ...destinations.map((b) => ({ name: `to:${b}`, borough: b, side: 'to' as const })),
    ];
    const index = new Map(nodes.map((n, i) => [n.name, i]));
    const links: Link[] = rows
      .filter((r) => Number(r.trips) > 0)
      .map((r) => ({
        source: index.get(`from:${r.pu_borough}`) ?? 0,
        target: index.get(`to:${r.do_borough}`) ?? 0,
        value: Number(r.trips),
        from: String(r.pu_borough),
        to: String(r.do_borough),
      }));
    if (links.length === 0) return null;

    const height = Math.max(260, Math.min(460, origins.length * 64 + 120));
    const layout = sankey<Node, Link>()
      .nodeWidth(12)
      .nodePadding(14)
      .extent([
        [1, 8],
        [width - 1, height - 8],
      ]);
    try {
      return { graph: layout({ nodes: nodes.map((n) => ({ ...n })), links: links.map((l) => ({ ...l })) }) as SankeyGraph<Node, Link>, height };
    } catch {
      // A degenerate graph (one node, or a cycle the layout cannot resolve)
      // should leave the panel empty rather than take the page down.
      return null;
    }
  }, [rows, width]);

  const legendItems = useMemo(() => {
    const boroughs = [...new Set(rows.map((r) => String(r.pu_borough)))].sort(
      (a, b) => (BOROUGH_SLOT[a] ?? 9) - (BOROUGH_SLOT[b] ?? 9),
    );
    return boroughs.map((borough) => ({ label: borough, color: seriesColor(theme, BOROUGH_SLOT[borough] ?? 6) }));
  }, [rows, theme]);

  const linkPath = sankeyLinkHorizontal<Node, Link>();

  return (
    <ChartCard
      testId="chart-borough-sankey"
      title="Where does a borough send its trips?"
      subtitle="Origin borough on the left, destination borough on the right, band thickness by trips."
      legend={<Legend items={legendItems} />}
      loading={query.loading}
      stale={query.loading && rows.length > 0}
      error={query.error}
      durationMs={query.durationMs}
      chart={
        <div ref={host} className="w-full" style={{ minHeight: 260 }}>
          {graph ? (
            <svg
              width={width}
              height={graph.height}
              viewBox={`0 0 ${width} ${graph.height}`}
              role="img"
              aria-label="Sankey of trips from origin borough to destination borough"
              style={{ overflow: 'visible' }}
            >
              <g>
                {graph.graph.links.map((link) => {
                  const key = `${link.from}:${link.to}`;
                  const dim = hovered !== null && hovered !== key;
                  return (
                    <path
                      key={key}
                      d={linkPath(link) ?? undefined}
                      fill="none"
                      stroke={seriesColor(theme, BOROUGH_SLOT[link.from] ?? 6)}
                      strokeWidth={Math.max(1, link.width ?? 1)}
                      strokeOpacity={dim ? 0.12 : 0.42}
                      onMouseEnter={() => setHovered(key)}
                      onMouseLeave={() => setHovered(null)}
                    >
                      <title>
                        {`${link.from} to ${link.to}\n${Number(link.value).toLocaleString('en-US')} trips\n${percent(
                          total > 0 ? link.value / total : 0,
                          2,
                        )} of the flows drawn`}
                      </title>
                    </path>
                  );
                })}
              </g>
              <g>
                {graph.graph.nodes.map((node) => (
                  <rect
                    key={node.name}
                    x={node.x0 ?? 0}
                    y={node.y0 ?? 0}
                    width={Math.max(2, (node.x1 ?? 0) - (node.x0 ?? 0))}
                    height={Math.max(1, (node.y1 ?? 0) - (node.y0 ?? 0))}
                    rx={2}
                    fill={seriesColor(theme, BOROUGH_SLOT[node.borough] ?? 6)}
                    stroke="var(--surface)"
                    strokeWidth={2}
                  >
                    <title>{`${node.borough}, ${node.side === 'from' ? 'as origin' : 'as destination'}\n${Number(
                      node.value ?? 0,
                    ).toLocaleString('en-US')} trips`}</title>
                  </rect>
                ))}
              </g>
              <g>
                {graph.graph.nodes.map((node) => {
                  const left = node.side === 'from';
                  return (
                    <text
                      key={`${node.name}-label`}
                      x={left ? (node.x1 ?? 0) + 6 : (node.x0 ?? 0) - 6}
                      y={((node.y0 ?? 0) + (node.y1 ?? 0)) / 2}
                      dominantBaseline="middle"
                      textAnchor={left ? 'start' : 'end'}
                      fontSize={11}
                      fill="var(--text-muted)"
                    >
                      {node.borough}
                    </text>
                  );
                })}
              </g>
            </svg>
          ) : (
            <p className="py-8 text-center text-xs" style={{ color: 'var(--text-faint)' }}>
              {query.loading ? 'Reading borough flows.' : 'No borough to borough flow survives the current filters.'}
            </p>
          )}
        </div>
      }
      table={
        <DataTable
          rows={rows}
          columns={[
            { key: 'pu_borough', label: 'From' },
            { key: 'do_borough', label: 'To' },
            { key: 'trips', label: 'Trips', align: 'right', render: (r) => Number(r.trips).toLocaleString('en-US') },
            { key: 'share', label: 'Share of drawn flows', align: 'right', render: (r) => percent(total > 0 ? Number(r.trips) / total : 0, 2) },
          ]}
          caption="Every borough pair with volume in the window."
          pageSize={20}
        />
      }
      footnote={
        <>
          {compactCount(total)} trips are on this diagram. A borough appears twice, once as an origin and
          once as a destination, and the two are different quantities: the left hand Manhattan node is
          trips that started there, the right hand one is trips that ended there. Band colour is the
          origin, fixed by borough rather than by size, so filtering does not repaint the diagram.
          agg_od_flow carries no day type, so that filter does not reach this chart, and the borough filter
          applies to the origin side only, which is why selecting one borough leaves a single band on the
          left and several on the right.
        </>
      }
    />
  );
}
