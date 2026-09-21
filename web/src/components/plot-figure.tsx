'use client';

import * as Plot from '@observablehq/plot';
import { useEffect, useRef, useState } from 'react';

export interface PlotFigureProps {
  /** Builds the Plot spec for a measured width. Kept as a callback so the chart
   *  can choose a different mark density at phone width rather than shrinking. */
  spec: (width: number) => Plot.PlotOptions;
  height?: number;
  ariaLabel: string;
  className?: string;
}

/**
 * Observable Plot renders to a detached node, so React owns the container and
 * Plot owns everything inside it. Re-rendering on width change rather than
 * scaling an svg keeps tick density honest at every size.
 */
export function PlotFigure({ spec, height, ariaLabel, className }: PlotFigureProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);

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

  useEffect(() => {
    const node = host.current;
    if (!node || width <= 0) return;
    let figure: (SVGSVGElement | HTMLElement) & { remove: () => void };
    try {
      figure = Plot.plot(spec(width));
    } catch {
      // A malformed spec must not take the page down with it; the panel's own
      // error state covers query failure, this covers a render failure.
      node.replaceChildren();
      return;
    }
    figure.setAttribute('role', 'img');
    figure.setAttribute('aria-label', ariaLabel);
    node.replaceChildren(figure);
    return () => {
      figure.remove();
    };
  }, [spec, width, ariaLabel]);

  return (
    <div
      ref={host}
      className={`cf-plot w-full ${className ?? ''}`}
      style={height ? { minHeight: height } : undefined}
    />
  );
}
