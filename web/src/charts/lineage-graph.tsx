'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '@/components/app-context';
import { seriesColor } from '@/lib/palette';
import type { Lineage, LineageNode, Metric } from '@/lib/types';

// Layer order from source to the panel that shows the number. Anything the
// manifest labels with something else lands in "other" and is drawn between the
// seeds and staging rather than dropped, because a node missing from a lineage
// diagram is worse than one in the wrong column.
const LAYER_ORDER = ['source', 'other', 'staging', 'intermediate', 'marts', 'exposure'] as const;
type Layer = (typeof LAYER_ORDER)[number];

const LAYER_LABEL: Record<Layer, string> = {
  source: 'Source and seed',
  other: 'Other',
  staging: 'Staging',
  intermediate: 'Intermediate',
  marts: 'Marts',
  exposure: 'Panel',
};

// Hue by layer, fixed, and deliberately skipping series-5: red on a node reads
// as a failure whatever the key says, and failure here is a state the graph does
// not model.
const LAYER_SLOT: Record<Layer, number> = {
  source: 6,
  other: 7,
  staging: 3,
  intermediate: 7,
  marts: 1,
  exposure: 2,
};

interface GraphNode {
  id: string;
  label: string;
  layer: Layer;
  description: string;
  x: number;
  y: number;
  column: number;
  onPath: boolean;
}

interface GraphEdge {
  from: string;
  to: string;
  onPath: boolean;
}

const NODE_WIDTH = 142;
const NODE_HEIGHT = 34;
const COLUMN_GAP = 44;
const ROW_GAP = 14;

// A seed is a static input file, so it shares the input column with the sources
// rather than earning one of its own: an extra column for two nodes pushes the
// panel column off the card and buys nothing. The node's type is still in its
// tooltip.
function layerOf(node: LineageNode): Layer {
  if (node.type === 'source' || node.type === 'seed') return 'source';
  const candidate = node.layer as Layer;
  return (LAYER_ORDER as readonly string[]).includes(candidate) ? candidate : 'other';
}

/**
 * The one hand built visualisation in this project. A layered directed graph is
 * a shape a library will always get almost right and never quite right: the
 * layers here are the dbt layers, not whatever a force simulation settles on,
 * and the highlighted path is the answer to the question the panel asks. That
 * is worth eighty lines of layout code.
 *
 * Layout is a simple layered assignment, not Sugiyama: with nineteen nodes and
 * a strict layer order there is nothing for a crossing minimisation pass to do
 * that ordering children under their parents does not already do.
 */
function buildGraph(
  lineage: Lineage,
  metric: Metric | null,
  width: number,
): { nodes: GraphNode[]; edges: GraphEdge[]; height: number; columns: { layer: Layer; x: number }[] } {
  const nodes = lineage.nodes;
  const exposures = lineage.exposures;

  // Every model, plus one pseudo node per exposure so the graph runs all the way
  // to the panel a reader is looking at.
  const all = new Map<string, { label: string; layer: Layer; description: string; parents: string[] }>();
  for (const [id, node] of Object.entries(nodes)) {
    all.set(id, { label: node.name, layer: layerOf(node), description: node.description, parents: node.depends_on });
  }
  for (const exposure of exposures) {
    all.set(`exposure.${exposure.name}`, {
      label: exposure.label,
      layer: 'exposure',
      description: exposure.description,
      parents: exposure.depends_on,
    });
  }

  // The path for the selected metric: its models, everything upstream of them,
  // and every exposure downstream that depends on one of them.
  const onPath = new Set<string>();
  if (metric) {
    const seeds = Object.keys(nodes).filter((id) => metric.models.includes(nodes[id]?.name ?? ''));
    const queue = [...seeds];
    while (queue.length > 0) {
      const id = queue.pop();
      if (!id || onPath.has(id)) continue;
      onPath.add(id);
      for (const parent of all.get(id)?.parents ?? []) queue.push(parent);
    }
    for (const exposure of exposures) {
      if (exposure.depends_on.some((dep) => onPath.has(dep))) onPath.add(`exposure.${exposure.name}`);
    }
  }

  const byLayer = new Map<Layer, string[]>();
  for (const [id, node] of all) {
    const list = byLayer.get(node.layer) ?? [];
    list.push(id);
    byLayer.set(node.layer, list);
  }

  const usedLayers = LAYER_ORDER.filter((layer) => (byLayer.get(layer)?.length ?? 0) > 0);
  const columnWidth = NODE_WIDTH + COLUMN_GAP;
  const needed = usedLayers.length * columnWidth;
  const offset = Math.max(0, (width - needed) / 2);

  const laidOut: GraphNode[] = [];
  let tallest = 0;
  usedLayers.forEach((layer, column) => {
    const ids = (byLayer.get(layer) ?? []).sort((a, b) => {
      // On path first, then alphabetical, so the highlighted chain reads as a
      // band across the top rather than a zigzag.
      const pathDelta = Number(onPath.has(b)) - Number(onPath.has(a));
      if (pathDelta !== 0) return pathDelta;
      return (all.get(a)?.label ?? a).localeCompare(all.get(b)?.label ?? b);
    });
    ids.forEach((id, row) => {
      const node = all.get(id);
      if (!node) return;
      laidOut.push({
        id,
        label: node.label,
        layer,
        description: node.description,
        x: offset + column * columnWidth,
        y: 30 + row * (NODE_HEIGHT + ROW_GAP),
        column,
        onPath: onPath.has(id),
      });
    });
    tallest = Math.max(tallest, ids.length);
  });

  const index = new Map(laidOut.map((n) => [n.id, n]));
  const edges: GraphEdge[] = [];
  for (const [id, node] of all) {
    for (const parent of node.parents) {
      if (!index.has(parent) || !index.has(id)) continue;
      edges.push({ from: parent, to: id, onPath: onPath.has(parent) && onPath.has(id) });
    }
  }

  return {
    nodes: laidOut,
    edges,
    height: 30 + tallest * (NODE_HEIGHT + ROW_GAP) + 20,
    columns: usedLayers.map((layer, column) => ({ layer, x: offset + column * columnWidth })),
  };
}

export function LineageGraph({ metric }: { metric: Metric | null }) {
  const { lineage, theme } = useApp();
  const host = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hovered, setHovered] = useState<GraphNode | null>(null);

  // The container is always mounted, even before the manifest lands, so the
  // observer attaches once and the measured width survives the arrival of the
  // data. An earlier version returned before the ref was rendered and the graph
  // stayed at zero width forever.
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

  const graph = useMemo(
    () => (lineage && width > 0 ? buildGraph(lineage, metric, Math.max(width, 930)) : null),
    [lineage, metric, width],
  );

  const nodeById = useMemo(() => new Map(graph?.nodes.map((n) => [n.id, n]) ?? []), [graph]);

  const inner = Math.max(width, 930);
  const unavailable = !lineage?.available;

  return (
    <div ref={host} className="w-full">
      {unavailable ? (
        <p className="py-6 text-center text-xs" style={{ color: 'var(--text-faint)' }}>
          No dbt manifest was published with this build, so the lineage graph has nothing to draw.
        </p>
      ) : null}
      <div className="cf-scroll overflow-x-auto" hidden={unavailable}>
        <svg
          width={inner}
          height={graph?.height ?? 240}
          viewBox={`0 0 ${inner} ${graph?.height ?? 240}`}
          role="img"
          aria-label={metric ? `Lineage from source to panel for the metric ${metric.name}` : 'dbt lineage graph'}
          data-testid="lineage-svg"
        >
          <g>
            {graph?.columns.map((column) => (
              <text
                key={column.layer}
                x={column.x + NODE_WIDTH / 2}
                y={16}
                textAnchor="middle"
                fontSize={10}
                fill="var(--text-faint)"
                style={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}
              >
                {LAYER_LABEL[column.layer]}
              </text>
            ))}
          </g>

          <g fill="none">
            {graph?.edges.map((edge) => {
              const from = nodeById.get(edge.from);
              const to = nodeById.get(edge.to);
              if (!from || !to) return null;
              const x1 = from.x + NODE_WIDTH;
              const y1 = from.y + NODE_HEIGHT / 2;
              const x2 = to.x;
              const y2 = to.y + NODE_HEIGHT / 2;
              const mid = (x1 + x2) / 2;
              const dim = hovered !== null && hovered.id !== edge.from && hovered.id !== edge.to;
              return (
                <path
                  key={`${edge.from}->${edge.to}`}
                  d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
                  stroke={edge.onPath ? seriesColor(theme, 1) : 'var(--axis)'}
                  strokeWidth={edge.onPath ? 2 : 1}
                  strokeOpacity={dim ? 0.15 : edge.onPath ? 0.85 : 0.5}
                  markerEnd="url(#lineage-arrow)"
                />
              );
            })}
          </g>

          <defs>
            <marker id="lineage-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="var(--axis)" />
            </marker>
          </defs>

          <g>
            {graph?.nodes.map((node) => {
              const accent = seriesColor(theme, LAYER_SLOT[node.layer]);
              const dim = hovered !== null && hovered.id !== node.id;
              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  onMouseEnter={() => setHovered(node)}
                  onMouseLeave={() => setHovered(null)}
                  style={{ cursor: 'default' }}
                >
                  <rect
                    width={NODE_WIDTH}
                    height={NODE_HEIGHT}
                    rx={4}
                    fill={node.onPath ? 'var(--panel-raised)' : 'var(--panel)'}
                    stroke={node.onPath ? accent : 'var(--border)'}
                    strokeWidth={node.onPath ? 2 : 1}
                    opacity={dim ? 0.45 : 1}
                  />
                  <rect width={3} height={NODE_HEIGHT} rx={1.5} fill={accent} opacity={node.onPath ? 1 : 0.35} />
                  <text
                    x={10}
                    y={NODE_HEIGHT / 2}
                    dominantBaseline="middle"
                    fontSize={10.5}
                    fill={node.onPath ? 'var(--text)' : 'var(--text-faint)'}
                  >
                    {node.label.length > 22 ? `${node.label.slice(0, 21)}...` : node.label}
                  </text>
                  <title>{`${node.label}\n${LAYER_LABEL[node.layer]}\n\n${node.description}`}</title>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-faint)' }}>
        {metric ? (
          <>
            The highlighted chain is everything{' '}
            <code style={{ color: 'var(--text-muted)' }}>{metric.name}</code> depends on, from the warehouse
            source through staging to{' '}
            {metric.models.join(', ')}, and on to the panels that display it. Nodes outside the chain are
            drawn faint rather than hidden, because what a number does not depend on is part of the answer.
          </>
        ) : (
          'Pick a metric to highlight its chain.'
        )}{' '}
        Hover a node for its description.
      </p>
    </div>
  );
}
