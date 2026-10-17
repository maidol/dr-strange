import { normalize, Store } from "./store";

/** An unrelated class whose `get` shares a name with Store.get. */
export class Cache {
  private hits = new Map<number, string>();

  get(key: number): string | undefined {
    return this.hits.get(key);
  }
}

export class Service {
  constructor(private store: Store) {}

  /** Through the interface: the implementation is a runtime fact. */
  load(key: string): string | undefined {
    return this.store.get(key);
  }

  /** Through the interface, plus a function whose target is written down. */
  save(key: string, value: string): void {
    this.store.put(key, normalize(value));
  }

  /** The parameter is annotated `Cache`, so the target is knowable. */
  warm(cache: Cache, key: number): string | undefined {
    return cache.get(key);
  }

  /** A call on `this` within the same class. */
  describe(): string {
    return this.label();
  }

  private label(): string {
    return "service";
  }
}
