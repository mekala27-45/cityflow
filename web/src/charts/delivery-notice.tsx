'use client';

import { useMemo } from 'react';
import { useApp } from '@/components/app-context';
import { ChartCard } from '@/components/chart-card';
import { DataTable } from '@/components/data-table';
import { deliveryReport } from '@/lib/duckdb-client';
import { bytes as formatBytes } from '@/lib/format';

/**
 * How each parquet file was actually read, shown only when that was not what
 * this page claims elsewhere.
 *
 * Every other panel here says the browser reads column chunks over HTTP range
 * requests. On a host that answers a range request with something other than
 * the bytes it names, that is not true, and a page that keeps saying it anyway
 * is the sort of thing the rest of this project exists to refuse. So when a
 * file has to be fetched whole instead, it says so, names the file, and says
 * what it cost.
 *
 * It renders nothing at all when every file came over ranges, which is the
 * normal case and the one this project is built around.
 */
export function DeliveryNotice() {
  const { engineReady } = useApp();

  const fallbacks = useMemo(
    () => (engineReady ? deliveryReport().filter((d) => d.mode === 'buffer') : []),
    [engineReady],
  );

  if (fallbacks.length === 0) return null;

  const total = fallbacks.reduce((sum, d) => sum + (d.bytes ?? 0), 0);

  return (
    <ChartCard
      title="Two of these files were not read the way the rest of this page describes"
      subtitle={
        `${fallbacks.length} of the shipped parquet files were downloaded whole rather than ` +
        `read by range, costing ${formatBytes(total)} this page did not need.`
      }
      chart={
        <DataTable
          rows={fallbacks.map((d) => ({
            file: d.file,
            downloaded: formatBytes(d.bytes ?? 0),
            why: d.reason ?? 'unknown',
          }))}
          columns={[
            { key: 'file', label: 'file' },
            { key: 'downloaded', label: 'downloaded', align: 'right' },
            { key: 'why', label: 'why the range path was refused' },
          ]}
        />
      }
      footnote={
        <>
          This is a delivery fault, not a data one. The copies in the repository are intact and every
          number on this page is the same either way. The host serving this site gzip compresses
          parquet responses, and for some objects its edge answers a byte range out of the compressed
          representation while labelling the response with the uncompressed length: the first few
          kilobytes arrive as zeroes, the tail arrives correct, and the reported size is right, so
          nothing upstream notices. DuckDB then reads a column chunk, gets zeroes, and fails inside
          the Thrift parser with a message that names neither the file nor the cause. Each file is
          checked for the parquet magic over a four byte range request before it is trusted, and the
          ones that fail that check are fetched whole, which this host does serve correctly.
        </>
      }
    />
  );
}
