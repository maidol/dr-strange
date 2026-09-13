#!/usr/bin/env python3
"""Does the usage report count the calls that go through the router?

Run:  python3 .claude/hooks/test_drsg_usage_report.py

No framework, because the hooks have none and one test file should not drag a
dependency into a directory that is otherwise stdlib-only. Exit status is the
result: 0 all passed, 1 something failed.

The fixtures are built here rather than read from `~/.claude/projects`: real
transcripts are pruned on `cleanupPeriodDays` (30 by default), so a test that
pointed at one would go green-then-vanish rather than green-then-red.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "drsg_usage_report.py"


def entry(*blocks: dict, role: str = "assistant") -> str:
    return json.dumps({"type": role, "message": {"role": role, "content": list(blocks)}})


def use(tool: str, uid: str, **inp) -> dict:
    return {"type": "tool_use", "id": uid, "name": tool, "input": inp}


def result(uid: str, text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": uid, "content": text}


def run(lines: list[str], name: str) -> str:
    """Run the hook over a throwaway transcript, return the systemMessage."""
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / f"{name}.jsonl"
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Isolate watermark files into the temporary directory via TMPDIR so
        # runs do not leave or touch any files in the system temp directory.
        env = {**os.environ, "TMPDIR": tmp}
        proc = subprocess.run(
            [sys.executable, "-I", str(HOOK)],
            input=json.dumps({"transcript_path": str(transcript)}),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    if proc.returncode != 0:
        raise AssertionError(f"hook exited {proc.returncode}: {proc.stderr}")
    return json.loads(proc.stdout)["systemMessage"]


ROUTED = [
    entry(use("mcp__codegraph__graph_context", "u1", repo="repo-a", name="Foo")),
    entry(result("u1", "x" * 400), role="user"),
    entry(use("mcp__codegraph__graph_impact", "u2", repo="repo-a", name="Foo")),
    entry(result("u2", "y" * 400), role="user"),
    # Bookkeeping, not a question put to a graph. Must stay uncounted.
    entry(use("mcp__drsg-events__event_post", "u3", recipient="/tmp", summary="s")),
    entry(result("u3", "posted"), role="user"),
    entry(use("Bash", "u4", command="grep -rn Foo backend/")),
    entry(result("u4", "hit"), role="user"),
]

DIRECT = [
    entry(use("mcp__drsg__context", "d1", name="Foo")),
    entry(result("d1", "z" * 400), role="user"),
    entry(use("mcp__drsg-watch__snippet", "d2", name="Foo")),
    entry(result("d2", "w" * 400), role="user"),
]

MIXED = ROUTED + DIRECT


def check(label: str, got: str, must: list[str], must_not: list[str]) -> bool:
    bad = [s for s in must if s not in got] + [s for s in must_not if s in got]
    print(("  ok   " if not bad else "  FAIL ") + label)
    if bad:
        print(f"         got: {got}")
        for s in bad:
            print(f"         offending: {s!r}")
    return not bad


def main() -> int:
    ok = True

    # The regression this exists for: routed calls were invisible, so the line
    # said the graph was never asked *and* scolded the shell it beat 2-to-1.
    got = run(ROUTED, "routed")
    ok &= check(
        "routed calls are counted",
        got,
        ["2 calls", "context×1", "impact×1"],
        ["has not been asked anything yet",
         "the shell was searched or read more than the graph was asked"],
    )

    # 2 and not 3: the to-do bookkeeping exclusion must survive the change.
    ok &= check("drsg-events stays excluded", got, ["2 calls"], ["event_post", "3 calls"])

    # The direct server must be untouched by any of this.
    ok &= check(
        "direct calls still counted",
        run(DIRECT, "direct"),
        ["2 calls", "context×1", "snippet×1"],
        ["has not been asked anything yet"],
    )

    # Routed and direct share a bucket, so two `context` calls read as ×2.
    ok &= check(
        "routed and direct share a verb bucket",
        run(MIXED, "mixed"),
        ["4 calls", "context×2"],
        ["graph_context"],
    )

    # A session that really did not ask must still say so -- otherwise the fix
    # would be indistinguishable from counting everything.
    ok &= check(
        "a session with no graph calls still says so",
        run([entry(use("Bash", "b1", command="ls")), entry(result("b1", "f"), role="user")], "none"),
        ["has not been asked anything yet"],
        ["1 call"],
    )

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
