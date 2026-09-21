// A Map preserves insertion order, so promoting a hit is a delete followed by a
// set and eviction is the first key the iterator yields. That is the whole cache.
// Entries hold parsed rows, not Arrow tables, because the tables hold WASM memory
// that we do not want pinned by a cache the user never sees.

export class Lru<V> {
  private store = new Map<string, V>();

  constructor(private readonly capacity: number) {}

  get(key: string): V | undefined {
    if (!this.store.has(key)) return undefined;
    const value = this.store.get(key) as V;
    this.store.delete(key);
    this.store.set(key, value);
    return value;
  }

  has(key: string): boolean {
    return this.store.has(key);
  }

  set(key: string, value: V): void {
    if (this.store.has(key)) this.store.delete(key);
    this.store.set(key, value);
    while (this.store.size > this.capacity) {
      const oldest = this.store.keys().next();
      if (oldest.done) break;
      this.store.delete(oldest.value);
    }
  }

  get size(): number {
    return this.store.size;
  }

  clear(): void {
    this.store.clear();
  }
}
