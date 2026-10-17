# Accuracy benchmark

[BENCHMARKS.md](../../BENCHMARKS.md) measures what the engine costs.
[AGENT-BENCHMARKS.md](../../AGENT-BENCHMARKS.md) measures how many round trips
an agent needs, and says plainly that its numbers are reported rather than
reproducible. This measures whether the answers are **right**, and everything
it needs is in this directory.

```console
$ uv run --no-project --with tomli python benchmarks/accuracy/crosscheck_go.py
$ uv run --no-project --with tomli python benchmarks/accuracy/score.py
```

## Three numbers, not one

A single "accuracy" percentage would hide the distinction that matters most.

- **Recall** — of the calls whose target is unambiguous from syntax alone, how
  many does the tool report? A miss is incompleteness: the agent asks who
  calls this and is told less than the source says.
- **Precision** — of the pairs the key marks as traps, how many does the tool
  assert anyway? A false edge is worse than a miss, because it sends an agent
  to rewrite code that was never involved.
- **Honesty** — at the sites where syntax genuinely cannot settle the target,
  does the tool say so or pick one and sound certain? Reported in parts: a
  **per-site** gap names the call it could not resolve; a **blanket**
  disclaimer says only that something may be missing. Both beat guessing; only
  the first tells you where.

## The corpus, and why it is a fixture

`corpus/<language>/` holds a small program written so that its hard cases are
the ones a real codebase has — an interface with two implementations, a
decorator holding the interface it decorates, a function value, a bound method
value, a closure, a deferred call, a goroutine, and a same-name method on an
unrelated type. Beside it, `key.toml` states which calls are real, which are
ambiguous, and which pairs must never be asserted, each with its reason.

A fixture rather than a real repository because ground truth has to come from
somewhere, and for a real tree it would come from whoever wrote the key.
The honest weakness of a fixture is the mirror image: **the author of the
fixture and the author of the key are the same person**, so a construct neither
thought of is in neither. Two things push against that:

1. The hard cases are taken from the language's own idioms and from what other
   code-graph tools document as their limits — not from what drsg is known to
   handle.
2. `crosscheck_go.py` checks the key against an independent tool.

## What the cross-check found, including about itself

`callgraph` (golang.org/x/tools) is the obvious oracle for Go, and it is not
usable as the key. Both its algorithms are wrong here, in opposite directions:

- **RTA** is reachability-based. It never sees a function `main` cannot reach,
  so `viaMethodValue`, `viaDefer` and `viaGo` are absent from its answer — not
  denied, absent.
- **CHA** sees every function and resolves a call through a function-typed
  variable to *every function in the universe with that signature*. For this
  fixture it claims `viaFuncValue` calls `strings.ToUpper`, `path.Base` and
  thirty others. None of that is true of the program.

So the cross-check is used as a bound, not as truth: an edge in `resolved`
must appear in at least one algorithm, and an edge in `absent` must appear in
neither RTA nor the key's own reasoning. On its first run it caught a real
defect — the key asserted `viaClosure -> upper`, while callgraph models the
closure as a synthetic `viaClosure$1` and routes the call through it. Those
synthetic nodes now collapse into their parent, which is the right reading for
a key that has no symbol for a closure.

## Results

drsg 2.22.0, Go plugin `go@2+029b2d9d`:

| | |
|---|---|
| Recall | **100%** — 14/14 unambiguous calls found |
| Precision | **100%** — 5/5 traps avoided, including the same-name method |
| Honesty | 6/6 ambiguous sites not guessed at: **2 named the gap**, 4 left only the blanket note |

### Known misses

Named here rather than left for a reader to discover:

- **A call through an interface-typed field leaves no trace.** `s.store.Get(id)`,
  where `store` is declared `Store` in the same file, produces neither a CALLS
  edge to `Store.Get` nor an `UnresolvedRef` naming the gap. An agent asking
  `context Service.Load` is told it has no callees, and learns only from the
  blanket lower-bound note that this may be incomplete. Four of the six
  ambiguous sites are this one shape. Pointing such a call at the interface
  method — which the parser already records as a node — would turn four
  blanket gaps into four named ones.

### Not yet covered

- **One language.** Go is first because it has an independent oracle. The other
  seven need their own corpus and key, and only Java has a comparable tool.
- **One tool.** The point of a tool-neutral vocabulary is to score codegraph
  and codebase-memory-mcp over the identical corpus; that driver is not
  written yet.
- **Call edges only.** `REFERENCES`, `IMPORTS`, `EXTENDS` and route matching
  are unscored.
