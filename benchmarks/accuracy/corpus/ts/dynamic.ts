import { normalize } from "./store";

/** A function held in a variable. */
export function viaFnValue(value: string): string {
  const f = normalize;
  return f(value);
}

/** An arrow function assigned, then called. */
export function viaArrow(value: string): string {
  const f = (v: string) => normalize(v);
  return f(value);
}

/** Indexed access: the method name is a string. */
export function viaIndex(obj: Record<string, () => string>, name: string): string {
  return obj[name]();
}

/** Optional chaining on a possibly-absent method. */
export function viaOptional(s?: { get(): string }): string | undefined {
  return s?.get();
}

/** The plain case, for contrast. */
export function direct(value: string): string {
  return normalize(value);
}
