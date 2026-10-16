#!/usr/bin/env python3
"""Two things about the documentation that nothing else checks.

**Every local reference resolves.** Markdown links and HTML `src`/`href`
attributes alike — the gallery in `README.zh.md` pointed at six deleted JPEGs
for six weeks because the commit that replaced them with PNG files updated only the
English README, and a link check that reads markdown syntax alone cannot see
an `<img src=…>`.

**The two READMEs describe the same product.** `docs/` has an EN/ZH parity
rule; the READMEs had none, and drifted the same way twice — a capability row
present in one edition only, and a plugin count corrected in one of them.
Parity here means structure, never wording: the same section headings in the
same order, the same number of capability rows, and the same images.

Standard library only, like the hooks beside it. Run it as `just gate-docs`,
or directly:

    python3 scripts/check_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Reference forms: a markdown link target, and an HTML src/href attribute.
MD_LINK = re.compile(r"\]\(([^)\s]+)")
HTML_REF = re.compile(r'(?:src|href)="([^"]+)"')
# A capability row in a README's feature table: `| **Name** | what it gives |`.
FEATURE_ROW = re.compile(r"^\| \*\*(.+?)\*\* \|")
HEADING = re.compile(r"^(#{2,3}) ")
SKIP = ("http://", "https://", "mailto:", "#")


def docs() -> list[Path]:
    """Every file the check reads: the READMEs, the two books, the reports."""
    found = [ROOT / "README.md", ROOT / "README.zh.md"]
    found += sorted(ROOT.glob("docs/*/src/*.md"))
    found += [p for p in (ROOT / "BENCHMARKS.md", ROOT / "AGENT-BENCHMARKS.md") if p.exists()]
    return [p for p in found if p.exists()]


def references(text: str) -> set[str]:
    return {r for r in MD_LINK.findall(text) + HTML_REF.findall(text)
            if not r.startswith(SKIP)}


def check_references(paths: list[Path]) -> list[str]:
    """A local reference must name a file, from the repo root or beside its page."""
    bad = []
    for path in paths:
        for ref in sorted(references(path.read_text(encoding="utf-8"))):
            target = ref.split("#")[0]
            if not target:
                continue
            if not ((ROOT / target).exists() or (path.parent / target).exists()):
                bad.append(f"{path.relative_to(ROOT)}: {ref} names no file")
    return bad


def check_readme_parity() -> list[str]:
    """The two editions must have the same shape, whatever the wording."""
    en = (ROOT / "README.md").read_text(encoding="utf-8").split("\n")
    zh = (ROOT / "README.zh.md").read_text(encoding="utf-8").split("\n")
    bad = []

    en_h = [l for l in en if HEADING.match(l)]
    zh_h = [l for l in zh if HEADING.match(l)]
    if len(en_h) != len(zh_h):
        bad.append(
            f"section count differs: README.md has {len(en_h)}, README.zh.md has {len(zh_h)}"
        )
    else:
        for a, b in zip(en_h, zh_h):
            # Depth must match even though the titles are in two languages.
            if a.split(" ")[0] != b.split(" ")[0]:
                bad.append(f"heading depth differs: {a!r} vs {b!r}")

    en_rows = [l for l in en if FEATURE_ROW.match(l)]
    zh_rows = [l for l in zh if FEATURE_ROW.match(l)]
    if len(en_rows) != len(zh_rows):
        bad.append(
            f"feature table differs: README.md lists {len(en_rows)} capabilities, "
            f"README.zh.md lists {len(zh_rows)} — a capability documented in one "
            f"language only is invisible to half the readers"
        )

    en_img = sorted(r for r in references("\n".join(en)) if r.startswith("screenshots/"))
    zh_img = sorted(r for r in references("\n".join(zh)) if r.startswith("screenshots/"))
    if en_img != zh_img:
        bad.append(f"galleries differ: README.md shows {en_img}, README.zh.md shows {zh_img}")
    return bad


def main() -> int:
    problems = check_references(docs()) + check_readme_parity()
    if problems:
        print("docs check failed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"docs: every local reference resolves across {len(docs())} files; "
          f"the two READMEs match in shape")
    return 0


if __name__ == "__main__":
    sys.exit(main())
