"""Every gate, with a test that breaks something on purpose.

Day 1's lesson: four of that build's thirteen gate tests asserted that the tree
as committed passes, and every one of them would have passed while two wrong
figures sat in the documents. So each gate here gets three tests: it passes on
a clean input, it fails on an input containing the exact defect it claims to
catch, and it refuses to report a pass when handed nothing to look at.

The third is the one people forget, and it is the one that let a whole package
through the em dash gate untouched earlier in this build.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def run(script: str, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *[str(a) for a in arguments]],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO,
    )


# ---------------------------------------------------------------------------
# The shipped size gate
# ---------------------------------------------------------------------------


def test_size_gate_passes_under_budget(tmp_path: Path) -> None:
    (tmp_path / "agg_small.parquet").write_bytes(b"0" * 1_000)
    result = run("check_shipped_size.py", "--dir", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_size_gate_fails_on_an_oversize_file(tmp_path: Path) -> None:
    """The deliberate violation: one file past the per file budget."""
    (tmp_path / "agg_huge.parquet").write_bytes(b"0" * 3_000_000)
    result = run("check_shipped_size.py", "--dir", tmp_path, "--file-budget-mb", "1")
    assert result.returncode == 1
    assert "agg_huge.parquet" in result.stdout
    assert "OVER" in result.stdout


def test_size_gate_fails_on_an_oversize_total(tmp_path: Path) -> None:
    for index in range(4):
        (tmp_path / f"agg_{index}.parquet").write_bytes(b"0" * 400_000)
    result = run(
        "check_shipped_size.py",
        "--dir",
        tmp_path,
        "--file-budget-mb",
        "10",
        "--total-budget-mb",
        "1",
    )
    assert result.returncode == 1
    assert "total budget" in result.stdout


def test_size_gate_rejects_an_unexpected_file_type(tmp_path: Path) -> None:
    (tmp_path / "agg_small.parquet").write_bytes(b"0" * 10)
    (tmp_path / "scratch.duckdb").write_bytes(b"0" * 10)
    result = run("check_shipped_size.py", "--dir", tmp_path)
    assert result.returncode == 1
    assert "scratch.duckdb" in result.stdout


def test_size_gate_will_not_pass_an_empty_directory(tmp_path: Path) -> None:
    result = run("check_shipped_size.py", "--dir", tmp_path)
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# The metric layer gate
# ---------------------------------------------------------------------------


def test_metric_gate_passes_on_the_real_tree() -> None:
    result = run("check_metric_layer.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_metric_gate_catches_an_aggregate_over_a_component(tmp_path: Path) -> None:
    """The deliberate violation: tip rate, recomputed in the page."""
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "sneaky.tsx").write_text(
        "const sql = `select sum(tip_obs_tip_sum) / sum(tip_obs_fare_sum) as tip_rate from agg`;\n",
        encoding="utf-8",
    )
    result = run("check_metric_layer.py", source, "--base", tmp_path)
    assert result.returncode == 1
    assert "aggregate over a component column" in result.stdout


def test_metric_gate_catches_arithmetic_between_components(tmp_path: Path) -> None:
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "sneaky.ts").write_text(
        "export const share = (row: Row) => row.tipped_trips / row.tip_obs_trips;\n",
        encoding="utf-8",
    )
    result = run("check_metric_layer.py", source, "--base", tmp_path)
    assert result.returncode == 1
    assert "arithmetic between two components" in result.stdout


def test_metric_gate_allows_row_level_display_arithmetic(tmp_path: Path) -> None:
    """A speed on one trip is not a metric, and the gate must not say it is."""
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "detail.tsx").write_text(
        "const mph = (t: Trip) => (t.trip_distance / t.duration_s) * 3600;\n",
        encoding="utf-8",
    )
    result = run("check_metric_layer.py", source, "--base", tmp_path)
    assert result.returncode == 0, result.stdout


def test_metric_gate_will_not_pass_an_empty_tree(tmp_path: Path) -> None:
    empty = tmp_path / "src"
    empty.mkdir()
    result = run("check_metric_layer.py", empty, "--base", tmp_path)
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# The published numbers gate
# ---------------------------------------------------------------------------


@pytest.fixture
def rendered_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A tiny template, manifest and rendered document, wired to tmp_path."""
    sys.path.insert(0, str(SCRIPTS))
    import check_published_numbers as gate

    template_dir = tmp_path / "docs" / "templates"
    template_dir.mkdir(parents=True)
    (template_dir / "REPORT.md").write_text(
        "The warehouse holds {{ n:fct_trip_rows }} trips "
        "and quarantined {{ f4:quarantine_share_pct }} percent.\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"claims": {"fct_trip_rows": 92_631_000, "quarantine_share_pct": 0.4567}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "REPO", tmp_path)
    monkeypatch.setattr(gate, "TEMPLATE_DIR", template_dir)
    monkeypatch.setattr(gate, "MANIFEST", manifest_path)
    return gate, tmp_path


def test_claim_gate_renders_then_passes(rendered_pair) -> None:  # type: ignore[no-untyped-def]
    gate, root = rendered_pair
    assert gate.main(["--write"]) == 0
    written = (root / "REPORT.md").read_text(encoding="utf-8")
    assert "92,631,000" in written
    assert "0.4567" in written
    assert gate.main([]) == 0


def test_claim_gate_catches_one_edited_digit(rendered_pair, capsys) -> None:  # type: ignore[no-untyped-def]
    """The deliberate violation, and the exact failure this gate exists for.

    On day 1 two figures like this were already committed before anyone
    checked.
    """
    gate, root = rendered_pair
    assert gate.main(["--write"]) == 0
    document = root / "REPORT.md"
    document.write_text(
        document.read_text(encoding="utf-8").replace("92,631,000", "92,631,001"),
        encoding="utf-8",
    )
    assert gate.main([]) == 1
    output = capsys.readouterr().out
    assert "disagrees with the warehouse" in output


def test_claim_gate_catches_a_deleted_document(rendered_pair) -> None:  # type: ignore[no-untyped-def]
    gate, root = rendered_pair
    assert gate.main(["--write"]) == 0
    (root / "REPORT.md").unlink()
    assert gate.main([]) == 1


def test_claim_gate_refuses_an_unknown_claim(rendered_pair) -> None:  # type: ignore[no-untyped-def]
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "{{ n:a_claim_nobody_measured }}\n", encoding="utf-8"
    )
    with pytest.raises(gate.ClaimError, match="No claim"):
        gate.main(["--write"])


def test_claim_gate_refuses_an_unknown_format(rendered_pair) -> None:  # type: ignore[no-untyped-def]
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "{{ pretty:fct_trip_rows }}\n", encoding="utf-8"
    )
    with pytest.raises(gate.ClaimError, match="Unknown format"):
        gate.main(["--write"])


def test_claim_gate_will_not_pass_with_no_templates(  # type: ignore[no-untyped-def]
    rendered_pair,
) -> None:
    gate, root = rendered_pair
    for path in (root / "docs" / "templates").glob("*.md"):
        path.unlink()
    assert gate.main([]) == 2
