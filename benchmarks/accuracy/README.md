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

## Four questions, not one number

A single "accuracy" percentage would hide the distinction that matters most.
The four arms are the four task types the annual plan names: callers, blast
radius, cross-file references, and same-name symbols (which is what the
precision traps are made of).

- **Recall** — of the calls whose target is unambiguous from syntax alone, how
  many does the tool report? A miss is incompleteness: the agent asks who
  calls this and is told less than the source says.
- **Precision** — of the pairs the key marks as traps, how many does the tool
  assert anyway? A false edge is worse than a miss, because it sends an agent
  to rewrite code that was never involved.
- **Impact** — "what would a change to this reach?", the question an agent
  asks before it edits anything. Scored as a set, by walking each tool's own
  edges backwards from a subject. This is where an edge error stops being one
  line in a report: a missing call hides a whole subtree of callers, and a
  false one drags in code that was never involved. A reach is only counted
  against a tool if the key cannot justify it *at all* — including through
  the candidates at an ambiguous site — so resolving `h = upper; h(s)` is
  treated as the extra capability it is, not as an invented edge.
- **Reference** — cross-file type references: a parameter typed by a struct
  three files away, a literal naming a type from another file. Recall only.
  There is no honest denominator for precision here, because what *else*
  counts as a reference — a method to its receiver, a same-file field, a file
  node to a type — is a modelling choice rather than a fact. A tool with no
  reference concept is reported as out of scope, not as a zero.
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
| C# | 89% (8/9) | **100%** (5/5) | 6/6 | 4 of 6 | Roslyn, in CI |
| Kotlin | 89% (8/9) | **100%** (5/5) | 5/5 | 4 of 5 | compiled bytecode |
| C | 86% (6/7) | **100%** (5/5) | 5/5 | 0 of 5, 1 followed | the object code |
| Rust | 83% (5/6) | **100%** (5/5) | 6/6 | 5 of 6 | `cargo expand`, macro only |

Every key is now checked by something that is not this project. C#'s oracle
runs in CI rather than locally — `.github/workflows/accuracy.yml`, because the
machine these fixtures were written on has no C# compiler — and its first run
found that the fixture **did not compile**: it leaned on a project's
`ImplicitUsings`, so `List<>` and `Func<,>` were unresolved to anything reading
the files directly, and it had already been scored by three tools in that
state. The check reports a compile failure rather than an empty graph, which
is the only reason that surfaced instead of passing as "no disagreements".

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

## Blast radius, and cross-file references

The two arms added last. Impact is derived from each tool's own edges, so it
is not an independent measurement — it is what the edge errors *cost* when a
question is asked through them.

| | impact | cross-file references |
|---|---|---|
| drsg | 56/61 | 15/21 |
| codegraph | 55/61 | — no reference edges |
| codebase-memory-mcp | 55/61 | 13/21 |

**On impact the three are close, and that is the honest result.** The arm
mostly re-reads the call edges, so it separates tools only where a false edge
compounds: codebase-memory-mcp's one wrong edge in the Go fixture
(`Cache.Get -> AuditStore.note`) puts **five** symbols in the blast radius of
`AuditStore.note` that never touch it — `Cache.Get`, `Service.Warm`,
`viaDefer`, `viaGo`, `viaMethodValue`. An agent asking what a change to
`note` affects is handed five files of unrelated work. That is the cost of a
false edge stated in the units an agent actually feels, and it is why
precision is the number to read first.

**On references drsg wins overall and loses where it is otherwise strongest.**

| | declared | drsg | codebase-memory-mcp |
|---|---|---|---|
| Go | 7 | 4 | **6** |
| C | 6 | 4 | 4 |
| Java | 4 | **4** | 3 |
| C#, Kotlin, Rust | 1 each | **1** each | 0 each |
| TypeScript | 1 | 0 | 0 |
| Python | 0 | — | — |

Three real gaps in drsg, all found by this arm and none of them known before:

1. **Composite literals (Go).** `&Service{store: &AuditStore{...}}` in `main`
   names three types from two other files. drsg records none of them: it sees
   types in declarations — fields, parameters — and not in literals.
   codebase-memory-mcp finds all three, which is why it wins that row.
2. **Struct fields (C).** `Store base`, `Store *inner`, `Store *store` are
   references to a type in `store.h`. drsg records the `Store *self`
   parameters and misses the fields.
3. **A constructor parameter (TypeScript).** `constructor(private store: Store)`
   is the one cross-file reference in that fixture, and drsg's TypeScript
   plugin records no type reference for it at all.

Two notes on what this arm is worth. **It is thin outside Go, Java and C** —
the other fixtures declare one reference each, because single-purpose files
with few types is what makes the call arm readable. And **Python declares
none**: its `Service.__init__(self, store)` carries no annotation, so there
is no cross-file type reference in that fixture to find. An empty arm there
is a fact about the code, not a tool failing to see something.

`IMPLEMENTS` counts as a reference for both tools — a class naming the
interface it implements is a reference to a type declared elsewhere,
whatever edge kind it lands on. Without that, Java's `MemStore -> Store`
read as a miss for drsg when it was recorded all along.

## Against the other two tools

The point of a tool-neutral key. Same fixture, same key, same questions,
scored by the same code — `--tool codegraph` or `--tool codebase-memory-mcp`.

drsg 2.21.0, codegraph 1.6.2, codebase-memory-mcp 0.12.1, every fixture:

| | drsg | codegraph | codebase-memory-mcp |
|---|---|---|---|
| C | 6/7 | 6/7 | **7/7** |
| C# † | 8/9 | 8/9 | **9/9**, 2 false |
| Go | **14/14** | 11/14 | 10/14, 2 false |
| Java | **7/7** | 5/7 | 5/7 |
| Kotlin | 8/9 | **9/9** | **9/9**, 2 false |
| Python | **8/8** | 7/8 | 7/8 |
| Rust | 5/6 | 5/6 | 4/6, 2 false |
| TypeScript | **7/7** | 6/7 | 6/7 |
| **total recall** | **63/67** | 57/67 | 57/67 |
| **false edges** | **0** | **0** | **8** |

Recall separates the three only a little. What separates them is the last row.

**codebase-memory-mcp asserts eight calls that do not exist**, and every one is
the same mistake: it picks a method by name without regard to the declared
type of what it was called on. From its own graph, for the Kotlin fixture:

```
MATCH (a)-[:CALLS]->(b) WHERE a.name = 'load'
  load  src/Service.kt:14  ->  get  src/Service.kt:7
```

Line 14 is `fun load(key: String): String? = store.get(key)`, where `store` is
typed `Store`. Line 7 is `fun get(key: Int): String? = hits[key]` — `Cache.get`,
which `load` never touches. It chose the `get` in the same file over the one
the declared type names, and it does this in C#, Go, Kotlin and Rust. In Go it
also has `Cache.Get` calling `AuditStore.note`, where `Cache.Get`'s entire body
is `return c.hits[key]`.

That is the failure this fixture was built to expose, and the one that costs
most: an agent told `Cache.Get` reaches `AuditStore.note` goes and reads code
that was never involved.

**codegraph records no calls into the standard library** — `callees normalize`
returns zero where the source calls `strings.TrimSpace`. That is a scope
decision rather than a resolution failure, and it accounts for three of its ten
misses. It asserts no false edge anywhere, and it is the only tool that
resolves both local function bindings in the Go fixture.

### Identity is by file and line, never by name

The three tools do not agree on what a symbol is called. drsg writes
`acme/orders.Cache.Get`; codegraph writes `Get` and distinguishes it from the
other two by printing `service.go:30`; codebase-memory-mcp qualifies an
interface method but leaves a struct's method bare. So every key records where
each symbol is declared, and each adapter maps whatever a tool returns back
through that. Matching on names would have measured this harness's
name-mangling and called it a competitor's accuracy.

An interface method to its own implementations is also **not** counted as a
false call: codegraph answers `callees Store.Get` with `MemStore.Get` and
`AuditStore.Get`, which is a defensible model of what an agent wants to know.

### The harness was wrong before it was right

Eleven times, the first result slandered a tool and the raw output disagreed.
Worth stating, because a comparative benchmark that has never been wrong has
probably not been checked. Nine of the eleven made a *competitor* look worse;
the last two, added with the reference arm, made **drsg** look worse — the
bug does not care which way it points:

1. codegraph prints a **subject's** location inline and a **callee's** on the
   next line; parsing only the second made every answer look off-subject, and
   `main` appeared to call nothing.
2. Querying the bare `Get` instead of `AuditStore.Get` answered about a
   different method, and its callees were attributed to the wrong symbol.
3. Retrying a legitimate "0 callees" with the bare name invented **nine**
   false edges for a tool that had asserted none.
4. A query matching several definitions prints **one block per definition**;
   folding them under the first subject invented eight more.
5. Externals have no line in the fixture, so three real stdlib edges were
   dropped as missing before name-matching was added for them.
6. Matching a path by basename worked only for the flat Go fixture and scored
   every nested corpus at **zero** — four languages reported as finding
   nothing at all.
7. `codegraph init` was run with its exit status ignored, so a failed index
   scored as a hopeless tool rather than a broken harness. It is fatal now.
8. Reusing one working directory let a tool's background daemon race the
   copy, and a key named `service.Service.save` was passed to a tool that
   wanted `Service.save` — TypeScript scored 14% on a fixture it indexes
   perfectly.
9. Deriving the other tool's project name by munging a path, instead of
   reading the name its own indexer returned, queried a project that did not
   exist and reported 0% for two more languages.
10. The reference arm resolved both ends of an edge through `[symbols]`,
   which holds only functions and methods. Every reference *points at a
   type*, so nothing resolved and codebase-memory-mcp scored 0 of 7 on a
   fixture where its own output plainly contained `main -> Service`. Types
   now live in a separate `[types]` table, kept out of the call universe so
   that a constructor edge does not become a false call.
11. The `[types]` entries were then written as bare names — `Service`, `Store`
   — while each key names symbols its own way (`service::Service` in Rust,
   `acme.Service` in Java). drsg scored **0% on seven of eight languages**
   for references it records perfectly. The C key had a second form of the
   same bug: its `strip` pattern covered `.c::` but not `.h::`, so a type
   declared in a header could never match. This is bug 8 again, which is the
   argument for the `[symbols]` table existing at all.

Every number above was checked against the tool's own raw output before being
written down. The rule that caught all eleven: **when a tool looks bad, read
what it actually printed** — and that rule has to apply to the tool this
benchmark is written for, or it is not a rule. Both new bugs were caught by
disbelieving a flattering-looking 0% for a rival and an unflattering 0% for
drsg, in the same way.

### Not yet covered

- **Rust's key rests mostly on reading the source.** `cargo expand` settles
  the macro entry; nothing independent checks the rest, because no call-graph
  oracle for Rust exists that is smaller than the fixture it would check. It
  is the one key here without a real second opinion.
- **The fixtures are small.** Eight programs of a few dozen lines, chosen for
  the shapes they contain rather than for scale. A tool that does well here
  may do worse on a million lines, and nothing here measures that.
- **Call edges only.** `REFERENCES`, `IMPORTS`, `EXTENDS` and route matching
  are unscored.
