"""Fail the build if an em dash, en dash or horizontal bar reaches a tracked file.

Ported from trajectory (day 1) and widened: that version scanned only Markdown
and Python. Analytics work writes SQL, YAML and TypeScript too, and a dash that
lands in a metric description is as visible as one in the README.

Box drawing characters are a different Unicode block and are left alone, so the
architecture diagrams survive. Files the tool cannot decode as UTF-8 are binary
and are skipped, which is reported rather than silently passed.

Usage:
    python scripts/check_no_em_dash.py            # scan the tracked tree
    python scripts/check_no_em_dash.py path ...   # scan specific paths
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# U+2014 em dash, U+2013 en dash, U+2015 horizontal bar, U+2212 minus sign.
# The minus sign is included because editors substitute it for a hyphen and it
# renders identically to an en dash in most sans-serif faces.
FORBIDDEN: dict[str, str] = {
    "—": "em dash (U+2014)",
    "–": "en dash (U+2013)",
    "―": "horizontal bar (U+2015)",
    "−": "minus sign (U+2212)",
}

SCANNED_SUFFIXES = frozenset(
    {
        ".py", ".md", ".sql", ".yml", ".yaml", ".toml", ".ts", ".tsx", ".js",
        ".jsx", ".css", ".json", ".txt", ".sh", ".cfg", ".ini", ".html",
    }
)

SKIP_DIRECTORIES = frozenset(
    {".git", "node_modules", ".venv", "target", "out", ".next", "dbt_packages", "__pycache__"}
)


def tracked_files(root: Path) -> list[Path]:
    """Every file git knows about, so untracked scratch and build output are ignored."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        # Not a git repository yet. Walk the tree instead so the gate still
        # works on the very first commit, which is when it is first wired in.
        return [p for p in root.rglob("*") if p.is_file()]
    names = result.stdout.decode("utf-8").split("\0")
    return [root / name for name in names if name]


def should_scan(path: Path) -> bool:
    if any(part in SKIP_DIRECTORIES for part in path.parts):
        return False
    return path.suffix in SCANNED_SUFFIXES


def scan(path: Path) -> list[tuple[int, int, str, str]]:
    """Return (line number, column, character description, the line) per hit."""
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[tuple[int, int, str, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for column, character in enumerate(line, start=1):
            if character in FORBIDDEN:
                hits.append((line_number, column, FORBIDDEN[character], line.strip()))
    return hits


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    if argv:
        candidates = [Path(a).resolve() for a in argv]
    else:
        candidates = tracked_files(root)

    total = 0
    scanned = 0
    for path in candidates:
        if not path.is_file() or not should_scan(path):
            continue
        scanned += 1
        for line_number, column, description, line in scan(path):
            total += 1
            relative = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"{relative}:{line_number}:{column}: {description}")
            print(f"    {line}")

    if total:
        print(f"\n{total} forbidden dash character(s) across {scanned} scanned files.")
        print("Use a comma, a colon, parentheses, or 'to' for ranges.")
        return 1

    print(f"No forbidden dash characters in {scanned} scanned files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
