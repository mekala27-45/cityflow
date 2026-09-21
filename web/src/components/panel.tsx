'use client';

import type { ReactNode } from 'react';

export const PANELS = [
  { id: 'pulse', short: 'Pulse', question: 'What does a normal week look like, and when did that change?' },
  { id: 'geography', short: 'Geography', question: 'Where do trips start and end, and how concentrated is that?' },
  { id: 'behavior', short: 'Trip behavior', question: 'How do trips differ by hour, and what does that cost the rider?' },
  { id: 'mix', short: 'Service mix', question: 'How have yellow, green and for hire vehicles traded share?' },
  { id: 'trust', short: 'Data health', question: 'Can you trust these numbers?' },
  { id: 'lineage', short: 'Lineage', question: 'Where does each number come from?' },
] as const;

export type PanelId = (typeof PANELS)[number]['id'];

interface PanelProps {
  id: PanelId;
  /** The panel heading is the question, in sentence case. Topics do not earn one. */
  question: string;
  /** One or two sentences on how to read the panel, not a restatement of it. */
  intro?: ReactNode;
  children: ReactNode;
}

export function Panel({ id, question, intro, children }: PanelProps) {
  return (
    <section
      id={id}
      data-testid={`panel-${id}`}
      className="scroll-mt-16 border-t pt-8 first:border-t-0 first:pt-0"
      style={{ borderColor: 'var(--border)' }}
      aria-labelledby={`${id}-heading`}
    >
      <header className="mb-5">
        <h2 id={`${id}-heading`} className="text-lg font-semibold tracking-tight sm:text-xl" style={{ color: 'var(--text)' }}>
          {question}
        </h2>
        {intro ? (
          <p className="mt-1.5 max-w-3xl text-sm leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            {intro}
          </p>
        ) : null}
      </header>
      <div className="flex flex-col gap-5">{children}</div>
    </section>
  );
}
