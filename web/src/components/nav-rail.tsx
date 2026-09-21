'use client';

import { useEffect, useState } from 'react';
import { PANELS, type PanelId } from './panel';

/**
 * A left rail on a wide screen, a horizontal strip on a narrow one. The active
 * entry follows the scroll position rather than the last click, so arriving at a
 * panel by scrolling still highlights it.
 */
export function NavRail() {
  const [active, setActive] = useState<PanelId>('pulse');

  useEffect(() => {
    const sections = PANELS.map((p) => document.getElementById(p.id)).filter(
      (node): node is HTMLElement => node !== null,
    );
    if (sections.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        const top = visible[0];
        if (top) setActive(top.target.id as PanelId);
      },
      { rootMargin: '-25% 0px -60% 0px', threshold: [0.05, 0.2, 0.5] },
    );
    for (const section of sections) observer.observe(section);
    return () => observer.disconnect();
  }, []);

  return (
    // min-w-0 matters: as a grid item the nav's automatic minimum size is its
    // content, and a row of six tabs is wider than a phone. Without it the whole
    // grid column stretches and the page scrolls sideways.
    <nav aria-label="Panels" className="min-w-0 lg:sticky lg:top-14 lg:self-start">
      <ol className="cf-scroll flex max-w-full gap-1 overflow-x-auto pb-1 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:pb-0">
        {PANELS.map((panel, index) => {
          const isActive = active === panel.id;
          return (
            <li key={panel.id} className="shrink-0">
              <a
                href={`#${panel.id}`}
                data-testid={`nav-${panel.id}`}
                aria-current={isActive ? 'true' : undefined}
                className="flex items-baseline gap-2 whitespace-nowrap rounded px-2.5 py-1.5 text-xs transition-colors lg:w-full"
                style={{
                  background: isActive ? 'var(--panel-raised)' : 'transparent',
                  color: isActive ? 'var(--text)' : 'var(--text-faint)',
                  borderLeft: isActive ? '2px solid var(--focus)' : '2px solid transparent',
                }}
              >
                <span className="tabular-nums" style={{ color: 'var(--text-faint)' }}>
                  {index + 1}
                </span>
                {panel.short}
              </a>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
