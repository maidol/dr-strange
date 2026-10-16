"""Tokens to answer "who calls this symbol, and what would a change reach?"

Two ways to answer the same question about the same symbols:

  graph  — one `drsg context <key>` call.
  text   — `rg -n <name>` over the tree, then reading every file that matched,
           which is what an agent without a graph does to see the call sites.

Symbols are selected by a stated rule, not by hand: functions this repository
defines (key prefix `dr_strange_`) whose recorded in-degree is 5..40 — the ones
someone might actually be about to change — sorted by key, then sampled at a
fixed stride so the set is deterministic and reproducible.

Two exclusions keep the comparison fair rather than flattering. Test functions
are dropped: nobody asks what calls a test. So are symbols whose final name is
a language-generic word (GENERIC below) — `new`, `open`, `run` and the like —
because the text baseline would have to grep that bare word, matching hundreds
of unrelated files, and no developer searches that way. Names shorter than
five characters go for the same reason. What remains is the
case text search handles *well*: a distinctive identifier, grepped with word
boundaries, which finds every real call site along with some noise.

Tokens are counted with tiktoken's cl100k_base, not estimated.

Reproduce (any repository with a plane, not just this one):

    drsg --db graph.drsg digest . --apply --code-only
    uv run --no-project --with tiktoken python benchmarks/token_cost.py \
        drsg graph.drsg <plane> .

Needs `rg` on PATH. The numbers in BENCHMARKS.md came from this script against
this repository; a different tree will give different ones, which is the point.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

DRSG, DB, PLANE = sys.argv[1], sys.argv[2], sys.argv[3]
ROOT = Path(sys.argv[4])
SAMPLE = 12

import tiktoken

ENC = tiktoken.get_encoding("cl100k_base")


def toks(s: str) -> int:
    return len(ENC.encode(s))


def run(args: list[str]) -> str:
    p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, check=False)
    return p.stdout


# Names too common for a bare grep to mean anything.
GENERIC = {
    "new", "open", "get", "set", "run", "len", "next", "from", "into",
    "default", "build", "parse", "clear", "close", "read", "write", "push",
    "drop", "main", "fmt", "name", "value", "init", "start", "stop", "send",
    "recv", "key", "id", "probe", "resolved", "apply", "list", "show", "count",
}


def candidates() -> list[str]:
    q = (
        "MATCH (caller)-[:CALLS]->(f:Function) "
        "RETURN key(f), count(*) AS n ORDER BY count(*) DESC LIMIT 400"
    )
    out = run([DRSG, "--db", DB, "cypher", "--plane", PLANE, q])
    rows = json.loads(out)["rows"]
    keys = [
        k
        for k, n in rows
        if k
        and k.startswith("dr_strange_")
        and 5 <= n <= 40
        and "test" not in k
        and k.rsplit("::", 1)[-1] not in GENERIC
        and len(k.rsplit("::", 1)[-1]) >= 5
    ]
    keys.sort()
    if len(keys) <= SAMPLE:
        return keys
    stride = len(keys) / SAMPLE
    return [keys[int(i * stride)] for i in range(SAMPLE)]


def text_answer(name: str) -> int:
    """`rg -n <name>`, plus every file that matched, read whole."""
    hits = run(["rg", "-nw", "--no-heading", name, "."])
    files = {line.split(":", 1)[0] for line in hits.splitlines() if ":" in line}
    total = toks(hits)
    for f in files:
        p = ROOT / f
        try:
            total += toks(p.read_text(errors="replace"))
        except (OSError, UnicodeError):
            pass
    return total, len(files)


def main() -> None:
    rows = []
    for key in candidates():
        ctx = run([DRSG, "--db", DB, "context", "--plane", PLANE, key])
        if not ctx.strip():
            continue
        name = key.rsplit("::", 1)[-1]
        text, nfiles = text_answer(name)
        g = toks(ctx)
        rows.append((key, g, text, nfiles, text / g if g else 0))

    print(f"{'symbol':<52} {'graph':>7} {'text':>9} {'files':>6} {'ratio':>7}")
    for key, g, t, nf, r in rows:
        short = key if len(key) <= 50 else "…" + key[-49:]
        print(f"{short:<52} {g:>7} {t:>9} {nf:>6} {r:>6.0f}x")
    ratios = [r for *_, r in rows]
    print(f"\nn={len(rows)}  median {statistics.median(ratios):.0f}x  "
          f"min {min(ratios):.0f}x  max {max(ratios):.0f}x")
    print(f"median tokens: graph {statistics.median(r[1] for r in rows):.0f}, "
          f"text {statistics.median(r[2] for r in rows):.0f}")


main()
