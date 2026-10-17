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

C's oracle is the object code. `objdump -d -r` distinguishes the two kinds of
call without any approximation at all: a direct call carries a symbol, and a
call through a struct of function pointers compiles to `call *%rcx`, naming
nothing — which is exactly the line the key draws.

Kotlin compiles to JVM bytecode, so `javap` serves it as it serves Java, with
three wrinkles the check accounts for: top-level functions live in a synthetic
`<File>Kt` class, every public function opens with the compiler's own
`Intrinsics.checkNotNullParameter`, and a lambda is reached by
`invokedynamic`.

Java's oracle is the compiler's own output. `javac` turns every call into an
explicit invoke instruction naming its target and `javap -c` prints them, so
the check reads what actually runs. Its bound is the same as TypeScript's —
bytecode records the *static* target, so an interface call is
`invokeinterface acme/Store.get` and never an implementation.

TypeScript has the strongest oracle of all, and it needs no approximation:
`crosscheck_ts.mjs` drives the compiler's own type checker — the same
authority an editor uses for go-to-definition. Its bound is the mirror of
RTA's: the checker answers for the *declared* type, so a call through an
interface resolves to the interface's method and never to an implementation.
That is exactly the line the key draws between `resolved` and `ambiguous`, so
it checks both sides of it.

Rust has none. rust-analyzer emits no call graph, and a MIR-level analysis
would be a larger undertaking than the fixture it would check. `crosscheck_rust.py`
therefore checks the single claim a tool can settle — that the `macro_rules!`
expands to a call to `normalize`, via `cargo expand` — and says plainly that
the rest of that key rests on reading the source. It is the weakest ground
here.

Python has no oracle of that kind — `pycg` does not install under a current
interpreter, and a type checker resolves types without emitting a call graph.
So `crosscheck_py.py` takes the other road: it exercises the fixture under
`sys.setprofile` and records the calls that **actually happen**. That is truth
rather than approximation, and it fails the opposite way: a dynamic trace
cannot over-report, only under-report by not exercising a path. Edges it never
reaches are printed as uncovered rather than counted as agreement, so a gap in
the exercise is visible instead of silent.

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

drsg 2.22.0, every language in the official catalog:

| | recall | precision | ambiguous sites not guessed | of those, gap **named** | key checked by |
|---|---|---|---|---|---|
| Go | **100%** (14/14) | **100%** (5/5) | 6/6 | 2 of 6 | `callgraph` (RTA + CHA) |
| Java | **100%** (7/7) | **100%** (5/5) | 6/6 | 4 of 6 | compiled bytecode |
| Python | **100%** (8/8) | **100%** (5/5) | 7/7 | 5 of 7 | a real execution trace |
| TypeScript | **100%** (7/7) | **100%** (5/5) | 7/7 | 0 of 7 | the TypeScript checker |
| C# † | 89% (8/9) | **100%** (5/5) | 6/6 | 4 of 6 | **nothing** |
| Kotlin | 89% (8/9) | **100%** (5/5) | 5/5 | 4 of 5 | compiled bytecode |
| C | 86% (6/7) | **100%** (5/5) | 5/5 | 0 of 5, 1 followed | the object code |
| Rust | 83% (5/6) | **100%** (5/5) | 6/6 | 5 of 6 | `cargo expand`, macro only |

† **C#'s key is unverified.** No C# compiler exists on the machine this was
written on — no dotnet SDK, and mono's `mcs` needs a package install the
account cannot perform — so that fixture has never been compiled and nothing
independent has confirmed its key. Read its row as provisional. `dotnet build`
plus `ildasm`/`monodis` would settle it, in the same shape as the Java check.

**Nothing was guessed, in any language, at any site where syntax cannot settle
the target. No false edge anywhere** — including every same-name trap, which a
tool matching on a bare method name would take.

### Where the plugins differ, which is the point of the last column

Precision is uniform and recall nearly so. What varies is whether a parser
*says* it could not resolve something, and that is a plugin decision rather
than a property of the engine.

- **Rust and Java point at the interface.** A `Box<dyn Store>` call becomes an
  edge to the trait method `Store::get`; a Java interface call becomes one to
  `acme.Store.get`. That names what was called without pretending to know
  which implementation runs — the same answer the TypeScript checker and the
  JVM's own bytecode give.
- **Python names the gap another way**, writing the unresolved expression into
  the key (`?::service.py::self.store.get`).
- **Go and TypeScript record nothing** for the same shape. For Go's
  `s.store.Get(id)` — where `store` is declared `Store` in the same file —
  there is neither an edge to `Store.Get` nor an unresolved reference. An agent
  asking `context Service.Load` is told it has no callees, and learns only from
  the blanket lower-bound note that this may be incomplete. TypeScript behaves
  the same way at all seven of its ambiguous sites.

That asymmetry is fixable rather than inherent. Three of the five plugins
already do what the other two do not, and in every case the parser has already
recorded the interface method as a node — Go's `Store.Get` and TypeScript's
`Store.get` are both in the graph, unconnected to the call that goes through
them.

### The recall misses, and they fall into two families

**A call a macro generates.** Rust's `via_macro` and C's `via_macro` both reach
`normalize` through a macro. `cargo expand` and the object code respectively
confirm the call; drsg records the enclosing function and, in C, an edge to the
macro's own name — but not to what the expansion calls. The call exists in no
line of source the parser reads.

**An extension method.** Kotlin's `viaExtension -> shout` and C#'s
`ViaExtension -> Shout` are the same miss in two languages: a call dispatched
statically by the receiver's type, which the bytecode resolves and the parser
does not. That two independently written plugins miss the identical shape
suggests the gap is in how extension dispatch is modelled rather than in either
plugin.

### One plugin follows a local binding

C resolves `f = normalize; f(value)` to `normalize`. That is not a guess — it
is the only binding in scope, and following it is local dataflow. It is scored
as its own outcome rather than as either a resolution or a guess, because the
other seven plugins leave the same shape unresolved. They are incomplete there,
not dishonest; C is more complete.

### Not yet covered

- **C#'s key is unverified**, as the table says. That is the one gap in
  coverage rather than a gap in languages.
- **One tool.** The point of a tool-neutral vocabulary is to score codegraph
  and codebase-memory-mcp over the identical corpus; that driver is not
  written yet, and until it is this measures drsg against ground truth rather
  than against anyone.
- **Call edges only.** `REFERENCES`, `IMPORTS`, `EXTENDS` and route matching
  are unscored.
