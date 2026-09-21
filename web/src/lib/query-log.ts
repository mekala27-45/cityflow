// Every query is timed and every byte the worker pulls is counted, because the
// README claims range requests rather than whole file downloads and a claim like
// that has to be checkable from outside. The log is mirrored onto
// window.__cityflowQueryLog so a benchmark script can read it without a hook.

export interface QueryRecord {
  id: number;
  label: string;
  sql: string;
  startedAt: number;
  durationMs: number;
  rows: number;
  bytes: number;
  requests: number;
  cached: boolean;
  error?: string;
}

export interface NetworkRecord {
  url: string;
  method: string;
  bytes: number;
  ranged: boolean;
  status: number;
  ms: number;
  at: number;
}

type Listener = () => void;

const MAX_RECORDS = 400;

declare global {
  interface Window {
    /** Ad hoc SQL against the live engine, for benchmarking and for checking a
     *  number on the page against the parquet without opening the app's code. */
    __cityflowQuery?: (sql: string) => Promise<unknown[]>;
    __cityflowQueryLog?: {
      queries: QueryRecord[];
      network: NetworkRecord[];
      engineReadyMs: number | null;
      firstPaintMs: number | null;
      totalBytes: number;
      rangedRequests: number;
      wholeFileRequests: number;
    };
  }
}

class QueryLog {
  // Both arrays are replaced rather than mutated on every append. Subscribers go
  // through useSyncExternalStore, which compares snapshots by identity: pushing
  // into a stable array leaves the reference unchanged and the benchmark panel
  // silently stops updating.
  private queries: readonly QueryRecord[] = [];
  private network: readonly NetworkRecord[] = [];
  private listeners = new Set<Listener>();
  private nextId = 1;
  private channel: BroadcastChannel | null = null;
  engineReadyMs: number | null = null;
  firstPaintMs: number | null = null;

  /** Bytes seen since a marker, so a single query can claim its own traffic. */
  private bytesSinceMark = 0;
  private requestsSinceMark = 0;

  attach(): void {
    if (typeof window === 'undefined' || this.channel) return;
    try {
      this.channel = new BroadcastChannel('cityflow-net');
      this.channel.onmessage = (event: MessageEvent) => {
        const raw = event.data as Partial<NetworkRecord> | null;
        if (!raw || typeof raw.url !== 'string') return;
        const record: NetworkRecord = {
          url: raw.url,
          method: String(raw.method ?? 'GET'),
          bytes: Number(raw.bytes ?? 0),
          ranged: Boolean(raw.ranged),
          status: Number(raw.status ?? 0),
          ms: Number(raw.ms ?? 0),
          at: Date.now(),
        };
        const next = [...this.network, record];
        this.network = next.length > MAX_RECORDS ? next.slice(next.length - MAX_RECORDS) : next;
        this.bytesSinceMark += record.bytes;
        this.requestsSinceMark += 1;
        this.publish();
      };
    } catch {
      // A browser without BroadcastChannel still runs; it just reports no bytes.
      this.channel = null;
    }
  }

  mark(): void {
    this.bytesSinceMark = 0;
    this.requestsSinceMark = 0;
  }

  claim(): { bytes: number; requests: number } {
    const claimed = { bytes: this.bytesSinceMark, requests: this.requestsSinceMark };
    this.bytesSinceMark = 0;
    this.requestsSinceMark = 0;
    return claimed;
  }

  record(entry: Omit<QueryRecord, 'id'>): QueryRecord {
    const full: QueryRecord = { ...entry, id: this.nextId++ };
    const next = [...this.queries, full];
    this.queries = next.length > MAX_RECORDS ? next.slice(next.length - MAX_RECORDS) : next;
    this.publish();
    return full;
  }

  setEngineReady(ms: number): void {
    this.engineReadyMs = ms;
    this.publish();
  }

  setFirstPaint(ms: number): void {
    if (this.firstPaintMs !== null) return;
    this.firstPaintMs = ms;
    this.publish();
  }

  getQueries(): readonly QueryRecord[] {
    return this.queries;
  }

  getNetwork(): readonly NetworkRecord[] {
    return this.network;
  }

  subscribe(fn: Listener): () => void {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  }

  private publish(): void {
    if (typeof window !== 'undefined') {
      const bodies = this.network.filter((n) => n.method !== 'HEAD');
      const ranged = bodies.filter((n) => n.ranged).length;
      window.__cityflowQueryLog = {
        queries: [...this.queries],
        network: [...this.network],
        engineReadyMs: this.engineReadyMs,
        firstPaintMs: this.firstPaintMs,
        totalBytes: this.network.reduce((acc, n) => acc + n.bytes, 0),
        rangedRequests: ranged,
        wholeFileRequests: bodies.length - ranged,
      };
    }
    for (const fn of this.listeners) fn();
  }
}

export const queryLog = new QueryLog();
