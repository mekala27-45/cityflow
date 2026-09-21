'use client';

import { useMemo, useState } from 'react';

export interface Column<Row> {
  key: string;
  label: string;
  align?: 'left' | 'right';
  render?: (row: Row) => string;
}

interface DataTableProps<Row> {
  rows: readonly Row[];
  columns: readonly Column<Row>[];
  caption?: string;
  pageSize?: number;
}

function cellText<Row>(row: Row, column: Column<Row>): string {
  if (column.render) return column.render(row);
  const value = (row as Record<string, unknown>)[column.key];
  if (value === null || value === undefined) return '';
  if (typeof value === 'number') return value.toLocaleString('en-US', { maximumFractionDigits: 4 });
  return String(value);
}

/** The rows behind a chart. Paged rather than virtualised: the tables a reader
 *  opens here are hundreds of rows, not hundreds of thousands. */
export function DataTable<Row>({ rows, columns, caption, pageSize = 25 }: DataTableProps<Row>) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pages - 1);
  const slice = useMemo(
    () => rows.slice(current * pageSize, current * pageSize + pageSize),
    [rows, current, pageSize],
  );

  return (
    <div className="w-full">
      <div className="cf-scroll overflow-x-auto rounded border" style={{ borderColor: 'var(--border)' }}>
        <table className="w-full min-w-[520px] text-xs tabular-nums">
          {caption ? (
            <caption className="px-3 pt-2 pb-1 text-left text-xs" style={{ color: 'var(--text-faint)' }}>
              {caption}
            </caption>
          ) : null}
          <thead>
            <tr style={{ background: 'var(--panel-raised)' }}>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  className={`px-3 py-2 font-medium ${column.align === 'right' ? 'text-right' : 'text-left'}`}
                  style={{ color: 'var(--text-muted)' }}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {slice.map((row, index) => (
              <tr key={index} style={{ borderTop: '1px solid var(--border)' }}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={`px-3 py-1.5 ${column.align === 'right' ? 'text-right' : 'text-left'}`}
                    style={{ color: 'var(--text)' }}
                  >
                    {cellText(row, column)}
                  </td>
                ))}
              </tr>
            ))}
            {slice.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-3 py-4 text-center" style={{ color: 'var(--text-faint)' }}>
                  No rows match the current filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
      {pages > 1 ? (
        <div className="mt-2 flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-40"
            style={{ borderColor: 'var(--border)' }}
            onClick={() => setPage(Math.max(0, current - 1))}
            disabled={current === 0}
          >
            Previous
          </button>
          <span>
            Rows {current * pageSize + 1} to {Math.min(rows.length, (current + 1) * pageSize)} of{' '}
            {rows.length.toLocaleString('en-US')}
          </span>
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-40"
            style={{ borderColor: 'var(--border)' }}
            onClick={() => setPage(Math.min(pages - 1, current + 1))}
            disabled={current >= pages - 1}
          >
            Next
          </button>
        </div>
      ) : (
        <p className="mt-2 text-xs" style={{ color: 'var(--text-faint)' }}>
          {rows.length.toLocaleString('en-US')} rows
        </p>
      )}
    </div>
  );
}
