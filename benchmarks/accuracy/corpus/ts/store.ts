// An accuracy fixture, not a module worth importing.
//
// TypeScript erases its types at runtime, so an annotation tells a parser the
// shape of a value without settling which implementation it is. Each case
// here is one a real codebase has.

export interface Store {
  get(key: string): string | undefined;
  put(key: string, value: string): void;
}

export class MemStore implements Store {
  private data = new Map<string, string>();

  get(key: string): string | undefined {
    return this.data.get(key);
  }

  put(key: string, value: string): void {
    this.data.set(key, value);
  }
}

/** A decorator holding the interface it decorates. */
export class AuditStore implements Store {
  private log: string[] = [];

  constructor(private inner: Store) {}

  get(key: string): string | undefined {
    this.note("get");
    return this.inner.get(key);
  }

  put(key: string, value: string): void {
    this.note("put");
    this.inner.put(key, value);
  }

  private note(entry: string): void {
    this.log.push(entry);
  }
}

/** A module-level function, called by name — the case that must not be missed. */
export function normalize(value: string): string {
  return value.trim();
}
