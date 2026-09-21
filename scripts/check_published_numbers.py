"""Re-derive every published figure and fail when a document disagrees.

Ported from trajectory (day 1), where re-verifying the published figures caught
two wrong numbers that had already been committed, and writing the gate caught
a third. On that build the gate parsed prose and looked for numbers near
markers, which worked and was fragile. This version does not parse prose.

Documents are rendered from templates in `docs/templates/`. Every figure in a
template is a placeholder naming a claim id in `web/public/data/manifest.json`,
and the manifest is produced by running queries against the warehouse. The gate
re-renders each template and compares byte for byte against the committed file.

That gives three properties the parsing version did not have:

- A number cannot be edited in a document without the gate noticing, because
  the comparison is the whole file and not a number near a marker.
- A number cannot be written in a document at all unless a query produces it.
- The failure message is a diff, so the fix is obvious.

Usage:
    python scripts/check_published_numbers.py           # check
    python scripts/check_published_numbers.py --write   # render
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO / "docs" / "templates"
MANIFEST = REPO / "web" / "public" / "data" / "manifest.json"

# {{ format:claim_id }}. The format is mandatory: a number rendered without a
# stated format is a number whose precision nobody decided.
PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_]+)\s*:\s*([a-z0-9_.]+)\s*\}\}")

# Column rendering for the table format: manifest key to (heading, formatter).
# A table in a document is still a set of figures, so it is rendered from the
# manifest for the same reason a single number is.
TABLE_COLUMNS: dict[str, list[tuple[str, str, str]]] = {
    "quarantine_by_rule": [
        ("rule", "rule", "code"),
        ("rows", "rows caught", "n"),
        ("share_pct", "share of source", "pct4"),
    ],
    "shipped": [
        ("file", "file", "code"),
        ("rows", "rows", "n"),
        ("bytes", "MB", "mb"),
    ],
    "bench": [
        ("panel", "panel", "raw"),
        ("query", "query", "raw"),
        ("rows", "rows returned", "n"),
        ("p50_ms", "p50 ms", "f2"),
        ("p95_ms", "p95 ms", "f2"),
        ("p99_ms", "p99 ms", "f2"),
        ("file_mb", "file MB", "f2"),
        ("row_groups", "row groups", "n"),
        ("pruned_pct", "pruned", "pct0"),
    ],
}


class ClaimError(RuntimeError):
    """A template refers to a claim that does not exist, or a format that does not."""


def _thousands(value: Any) -> str:
    return f"{int(value):,}"


def _plain(value: Any) -> str:
    return str(value)


def _fixed(places: int):  # type: ignore[no-untyped-def]
    def render(value: Any) -> str:
        return f"{float(value):,.{places}f}"

    return render


def _date(value: Any) -> str:
    if isinstance(value, str):
        return dt.date.fromisoformat(value).strftime("%B %Y")
    return str(value)


def _iso(value: Any) -> str:
    return str(value)


def _cell(value: Any, style: str) -> str:
    if value is None:
        return ""
    if style == "code":
        return f"`{value}`"
    if style == "n":
        return f"{int(value):,}"
    if style == "mb":
        return f"{int(value) / 1_000_000:,.2f}"
    if style == "f2":
        return f"{float(value):,.2f}"
    if style == "pct4":
        return f"{float(value):.4f} percent"
    if style == "pct0":
        return f"{float(value):.0f} percent"
    return str(value)


def _table(rows: Any, key: str) -> str:
    """Render a list of dictionaries from the manifest as a markdown table."""
    if not isinstance(rows, list) or not rows:
        raise ClaimError(f"Claim {key!r} is not a non empty list; cannot table it.")
    columns = TABLE_COLUMNS.get(key)
    if columns is None:
        raise ClaimError(
            f"No column definition for table {key!r}. Add one to TABLE_COLUMNS, "
            "so the heading and the precision of every column is decided once."
        )
    lines = ["| " + " | ".join(heading for _, heading, _ in columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        lines.append(
            "| " + " | ".join(_cell(row.get(field), style) for field, _, style in columns) + " |"
        )
    return "\n".join(lines)


FORMATS: dict[str, Any] = {
    "n": _thousands,
    "raw": _plain,
    "f0": _fixed(0),
    "f1": _fixed(1),
    "f2": _fixed(2),
    "f3": _fixed(3),
    "f4": _fixed(4),
    "month": _date,
    "iso": _iso,
    # Handled before the lookup, listed so the format check accepts it.
    "table": _plain,
}


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    # Resolved at call time, not bound as a default, so a test can point the
    # gate at a fixture without patching the caller.
    path = path or MANIFEST
    if not path.is_file():
        raise ClaimError(
            f"{path} is missing. Run 'cityflow manifest' first: every figure in "
            "the documents is re-derived from it and there is nothing to check "
            "against."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _lookup(manifest: dict[str, Any], claim_id: str) -> Any:
    claims: dict[str, Any] = manifest.get("claims", {})
    if claim_id in claims:
        return claims[claim_id]
    # Allow dotted access into the rest of the manifest, for the window and
    # the backend, so a document can state them without a bespoke claim.
    cursor: Any = manifest
    for part in claim_id.split("."):
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            raise ClaimError(
                f"No claim {claim_id!r} in the manifest. Defined claims: "
                + ", ".join(sorted(claims))
            )
    return cursor


def render(template: str, manifest: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        fmt, claim_id = match.group(1), match.group(2)
        if fmt not in FORMATS:
            raise ClaimError(
                f"Unknown format {fmt!r} for claim {claim_id!r}. "
                f"Known: {', '.join(sorted(FORMATS))}"
            )
        if fmt == "table":
            return _table(_lookup(manifest, claim_id), claim_id)
        value = _lookup(manifest, claim_id)
        if value is None:
            raise ClaimError(
                f"Claim {claim_id!r} is null in the manifest. The query that "
                "produces it returned nothing, so the document cannot state it."
            )
        return str(FORMATS[fmt](value))

    return PLACEHOLDER.sub(replace, template)


def targets() -> list[tuple[Path, Path]]:
    """Every template and the file it renders to.

    A template at docs/templates/README.md renders to README.md at the
    repository root. One at docs/templates/docs/data.md renders to docs/data.md.
    """
    out: list[tuple[Path, Path]] = []
    for template in sorted(TEMPLATE_DIR.rglob("*.md")):
        relative = template.relative_to(TEMPLATE_DIR)
        out.append((template, REPO / relative))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Render the documents instead of checking."
    )
    args = parser.parse_args(argv)

    manifest = load_manifest()
    pairs = targets()
    if not pairs:
        print(
            f"No templates found under {TEMPLATE_DIR}. Refusing to report a pass.",
            file=sys.stderr,
        )
        return 2

    failures = 0
    total_claims = 0
    for template_path, output_path in pairs:
        template = template_path.read_text(encoding="utf-8")
        total_claims += len(PLACEHOLDER.findall(template))
        rendered = render(template, manifest)

        if args.write:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
            print(f"wrote {output_path.relative_to(REPO)}")
            continue

        if not output_path.is_file():
            print(f"{output_path.relative_to(REPO)} has not been rendered.")
            failures += 1
            continue

        current = output_path.read_text(encoding="utf-8")
        if current != rendered:
            failures += 1
            print(f"\n{output_path.relative_to(REPO)} disagrees with the warehouse:")
            diff = difflib.unified_diff(
                current.splitlines(),
                rendered.splitlines(),
                fromfile=f"committed/{output_path.name}",
                tofile=f"re-derived/{output_path.name}",
                lineterm="",
                n=1,
            )
            for line in list(diff)[:60]:
                print(f"  {line}")

    if args.write:
        print(f"\nRendered {len(pairs)} documents from {total_claims} claims.")
        return 0

    if failures:
        print(
            f"\n{failures} document(s) disagree with the manifest. "
            "Run 'make docs' to re-render, then read the diff before committing: "
            "a number that moved unexpectedly is the point of this gate."
        )
        return 1

    print(f"{len(pairs)} documents match the warehouse across {total_claims} figures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
