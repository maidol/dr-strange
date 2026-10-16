/**
 * Wires: the routes a plane states, and which of them meet.
 *
 * An HTTP route is the one reference a parser cannot turn into an edge — the
 * handler and the caller are often in different languages and repositories,
 * and between them there is only a string. The plugins write what each side
 * states (`serves` on a handler, `requests` on a caller) and the match is
 * computed when someone asks, never stored. This module is the browser's half
 * of that: it reads what the plugins wrote and applies core's own rule.
 */

/** A described property value (`{ $desc, $value }`) reduced to its value. */
export function unwrap(v) {
  return v != null && typeof v === 'object' && '$value' in v ? v.$value : v
}

/** A property holding a list of strings, or an empty list if it holds anything else. */
export function propList(v) {
  const inner = unwrap(v)
  return Array.isArray(inner) ? inner.filter((x) => typeof x === 'string') : []
}

/**
 * Every route stated by these nodes, as `{ side, verb, path, written, … }`.
 *
 * `VERB path` is the whole contract with the plugins that write it, so a value
 * shaped otherwise was not written by one and is skipped rather than guessed
 * at. `*_as_written` carries the path before normalising, for display.
 */
export function statedFrom(nodes) {
  const out = []
  for (const n of nodes ?? []) {
    for (const side of ['serves', 'requests']) {
      const routes = propList(n.properties?.[side])
      const written = propList(n.properties?.[`${side}_as_written`])
      routes.forEach((route, i) => {
        const sp = route.indexOf(' ')
        if (sp <= 0) return
        out.push({
          side,
          verb: route.slice(0, sp),
          path: route.slice(sp + 1),
          written: written[i] ?? null,
          id: n.id,
          key: n.external_key ?? `#${n.id}`,
          label: n.labels?.[0] ?? '',
          file: unwrap(n.properties?.file) ?? null,
          line: unwrap(n.properties?.line) ?? null,
        })
      })
    }
  }
  return out
}

/**
 * Whether one stated route answers another.
 *
 * Core's rule, kept in step with `Stated::answers` in `compact.rs`: equal
 * paths, and verbs that are equal or either one `*` — a side naming no verb
 * matches every verb on that path. The paths compare as plain strings because
 * the plugins normalised them at write time.
 */
export function answers(a, b) {
  return a.path === b.path && (a.verb === b.verb || a.verb === '*' || b.verb === '*')
}

/**
 * Each request paired with every declaration that answers it.
 *
 * A request nothing answers is kept, with an empty list: an unanswered call is
 * the more interesting half of this view.
 */
export function matchRequests(shown, all) {
  return shown
    .filter((w) => w.side === 'requests')
    .map((req) => ({ req, serves: all.filter((s) => s.side === 'serves' && answers(s, req)) }))
}
