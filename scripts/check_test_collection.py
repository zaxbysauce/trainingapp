"""Defect-class guardrail for issue #51: fail if any test file is invisible to CI.

The /ask/stream bug survived because its only behavioral tests lived in
root-level `test_*.py` files while pytest.ini's `testpaths` (and the CI
command `pytest tests/`) never collected them. This check fails when a
`test_*.py` file exists outside the set of paths pytest would collect, so
"test exists but CI never runs it" cannot recur silently.

Allowed extra roots come from pytest.ini's `testpaths` (parsed with a small
regex, no pytest import needed). Files under node_modules/.agents/etc. are
ignored via the same excludes as the corpus.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INI = ROOT / "pytest.ini"
IGNORED_DIRS = {
    ".git",
    ".agents",
    ".github",
    ".claude",
    ".codex",
    ".opencode",
    ".swarm",
    ".zcode",
    "node_modules",
    "graphify-out",
    "dist",
    "build",
    "__pycache__",
    "docs",
    "models",
    "contracts",
}


def collected_roots() -> set[Path]:
    if not INI.exists():
        print(f"check_test_collection: {INI} missing — cannot derive testpaths")
        sys.exit(2)
    testpaths: set[Path] = set()
    for line in INI.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*testpaths\s*=\s*(.+?)\s*$", line)
        if m:
            for entry in m.group(1).split():
                testpaths.add((ROOT / entry).resolve())
    if not testpaths:
        print("check_test_collection: pytest.ini has no testpaths — cannot derive")
        sys.exit(2)
    return testpaths


def main() -> int:
    roots = collected_roots()
    orphans: list[Path] = []
    for path in sorted(ROOT.rglob("test_*.py")):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        resolved = path.resolve()
        if any(resolved == root or root in resolved.parents for root in roots):
            continue
        orphans.append(resolved)

    if orphans:
        print(
            "check_test_collection: FAIL — test files exist outside pytest "
            "testpaths (CI never runs them). Move them under a collected path "
            "or extend testpaths:"
        )
        for o in orphans:
            print(f"  {o.relative_to(ROOT)}")
        return 1
    print(
        "check_test_collection: OK — every test_*.py lives under "
        + ", ".join(str(r.relative_to(ROOT)) for r in sorted(roots))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
