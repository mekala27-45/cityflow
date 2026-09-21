"""Every gate, with a test that breaks something on purpose.

Day 1's lesson: four of that build's thirteen gate tests asserted that the tree
as committed passes, and every one of them would have passed while two wrong
figures sat in the documents. So each gate here gets three tests: it passes on
a clean input, it fails on an input containing the exact defect it claims to
catch, and it refuses to report a pass when handed nothing to look at.

The third is the one people forget, and it is the one that let a whole package
through the em dash gate untouched earlier in this build.

The gates are called in process. Each one is written as `main(argv) -> int`
behind a `raise SystemExit(main(...))` guard precisely so it can be, and a gate
run through `subprocess.run` is a gate coverage cannot see: it reads as dead
code in the report and dead code is what gets deleted. One subprocess test per
script stays, so the entry point and the exit code plumbing keep a test too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import check_metric_layer
import check_shipped_size
import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


@dataclass(frozen=True, slots=True)
class GateRun:
    """What a gate did: the exit code it returned and what it printed."""

    returncode: int
    stdout: str
    stderr: str


def run_gate(
    main: Callable[[list[str]], int],
    capsys: pytest.CaptureFixture[str],
    *arguments: str | Path,
) -> GateRun:
    """Call a gate's main in process and collect the same three things a
    subprocess would have given us."""
    code = main([str(a) for a in arguments])
    captured = capsys.readouterr()
    return GateRun(returncode=code, stdout=captured.out, stderr=captured.err)


def run_as_script(script: str, *arguments: str | Path) -> subprocess.CompletedProcess[str]:
    """The out of process path, kept for the entry point tests."""
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


def test_size_gate_passes_under_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "agg_small.parquet").write_bytes(b"0" * 1_000)
    result = run_gate(check_shipped_size.main, capsys, "--dir", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


def test_size_gate_fails_on_an_oversize_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deliberate violation: one file past the per file budget."""
    (tmp_path / "agg_huge.parquet").write_bytes(b"0" * 3_000_000)
    result = run_gate(check_shipped_size.main, capsys, "--dir", tmp_path, "--file-budget-mb", "1")
    assert result.returncode == 1
    assert "agg_huge.parquet" in result.stdout
    assert "OVER" in result.stdout


def test_size_gate_fails_on_an_oversize_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for index in range(4):
        (tmp_path / f"agg_{index}.parquet").write_bytes(b"0" * 400_000)
    result = run_gate(
        check_shipped_size.main,
        capsys,
        "--dir",
        tmp_path,
        "--file-budget-mb",
        "10",
        "--total-budget-mb",
        "1",
    )
    assert result.returncode == 1
    assert "total budget" in result.stdout


def test_size_gate_rejects_an_unexpected_file_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "agg_small.parquet").write_bytes(b"0" * 10)
    (tmp_path / "scratch.duckdb").write_bytes(b"0" * 10)
    result = run_gate(check_shipped_size.main, capsys, "--dir", tmp_path)
    assert result.returncode == 1
    assert "scratch.duckdb" in result.stdout


def test_size_gate_will_not_pass_an_empty_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = run_gate(check_shipped_size.main, capsys, "--dir", tmp_path)
    assert result.returncode == 2


def test_size_gate_will_not_pass_a_directory_that_is_not_there(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A path typo must not read as a clean shipped layer."""
    result = run_gate(check_shipped_size.main, capsys, "--dir", tmp_path / "absent")
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_size_gate_reads_the_budget_from_the_committed_config() -> None:
    """The budget the gate applies is the one the pipeline was built with."""
    file_budget, total_budget = check_shipped_size.budgets()
    assert file_budget > 0
    assert total_budget >= file_budget


def test_size_gate_entry_point_runs_as_a_script(tmp_path: Path) -> None:
    """One subprocess run, so the __main__ guard keeps a test of its own."""
    (tmp_path / "agg_small.parquet").write_bytes(b"0" * 1_000)
    result = run_as_script("check_shipped_size.py", "--dir", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# The metric layer gate
# ---------------------------------------------------------------------------


def test_metric_gate_passes_on_the_real_tree(capsys: pytest.CaptureFixture[str]) -> None:
    result = run_gate(check_metric_layer.main, capsys)
    assert result.returncode == 0, result.stdout + result.stderr


def test_metric_gate_catches_an_aggregate_over_a_component(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The deliberate violation: tip rate, recomputed in the page."""
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "sneaky.tsx").write_text(
        "const sql = `select sum(tip_obs_tip_sum) / sum(tip_obs_fare_sum) as tip_rate from agg`;\n",
        encoding="utf-8",
    )
    result = run_gate(check_metric_layer.main, capsys, source, "--base", tmp_path)
    assert result.returncode == 1
    assert "aggregate over a component column" in result.stdout


def test_metric_gate_catches_arithmetic_between_components(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "sneaky.ts").write_text(
        "export const share = (row: Row) => row.tipped_trips / row.tip_obs_trips;\n",
        encoding="utf-8",
    )
    result = run_gate(check_metric_layer.main, capsys, source, "--base", tmp_path)
    assert result.returncode == 1
    assert "arithmetic between two components" in result.stdout


def test_metric_gate_allows_row_level_display_arithmetic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A speed on one trip is not a metric, and the gate must not say it is."""
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "detail.tsx").write_text(
        "const mph = (t: Trip) => (t.trip_distance / t.duration_s) * 3600;\n",
        encoding="utf-8",
    )
    result = run_gate(check_metric_layer.main, capsys, source, "--base", tmp_path)
    assert result.returncode == 0, result.stdout


def test_metric_gate_ignores_a_violation_inside_a_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A comment describing the forbidden expression is not the expression."""
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "commented.ts").write_text(
        "// never write sum(tip_obs_tip_sum) here\n"
        " * row.tipped_trips / row.tip_obs_trips is what the layer defines\n"
        "export const ok = 1;\n",
        encoding="utf-8",
    )
    result = run_gate(check_metric_layer.main, capsys, source, "--base", tmp_path)
    assert result.returncode == 0, result.stdout


def test_metric_gate_skips_the_allowlisted_catalog_module(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The catalog module splices the layer's own SQL, which is the mechanism
    the gate protects rather than a violation of it."""
    catalog = tmp_path / "src" / "lib"
    catalog.mkdir(parents=True)
    (catalog / "metrics.ts").write_text(
        "const sql = `select sum(tip_obs_tip_sum) from agg`;\n", encoding="utf-8"
    )
    result = run_gate(check_metric_layer.main, capsys, tmp_path / "src", "--base", tmp_path)
    assert result.returncode == 0, result.stdout


def test_metric_gate_will_not_pass_an_empty_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "src"
    empty.mkdir()
    result = run_gate(check_metric_layer.main, capsys, empty, "--base", tmp_path)
    assert result.returncode == 2


def test_metric_gate_refuses_a_metrics_file_naming_no_components(tmp_path: Path) -> None:
    """Nothing to protect is not the same as nothing to find."""
    empty = tmp_path / "metrics.yml"
    empty.write_text("metrics: []\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="names no components"):
        check_metric_layer.components(empty)


def test_metric_gate_entry_point_runs_as_a_script(tmp_path: Path) -> None:
    """One subprocess run, so the __main__ guard keeps a test of its own."""
    source = tmp_path / "src" / "charts"
    source.mkdir(parents=True)
    (source / "clean.ts").write_text("export const n = 1;\n", encoding="utf-8")
    result = run_as_script("check_metric_layer.py", source, "--base", tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# The published numbers gate
# ---------------------------------------------------------------------------


@pytest.fixture
def rendered_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A tiny template, manifest and rendered document, wired to tmp_path."""
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
        json.dumps(
            {
                "claims": {
                    "fct_trip_rows": 92_631_000,
                    "quarantine_share_pct": 0.4567,
                    "first_period": "2021-07-01",
                    "generated_at": "2026-09-21T00:00:00+00:00",
                    "nothing_measured": None,
                },
                "window": {"start": "2021-07-01"},
                "backend": ["synthetic"],
                "quarantine_by_rule": [{"rule": "unknown_zone", "rows": 1234, "share_pct": 0.5432}],
                "shipped": [{"file": "agg_daily.parquet", "rows": None, "bytes": 2_500_000}],
                "bench": [
                    {
                        "panel": "pulse",
                        "query": "KPI tiles",
                        "rows": 12,
                        "p50_ms": 1.5,
                        "p95_ms": 2.5,
                        "p99_ms": 3.5,
                        "file_mb": 3.51,
                        "row_groups": 8,
                        "pruned_pct": 75.0,
                    }
                ],
            }
        ),
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


def test_claim_gate_refuses_a_claim_the_query_returned_null_for(  # type: ignore[no-untyped-def]
    rendered_pair,
) -> None:
    """A document cannot state a figure no query produced."""
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "{{ n:nothing_measured }}\n", encoding="utf-8"
    )
    with pytest.raises(gate.ClaimError, match="is null in the manifest"):
        gate.main(["--write"])


def test_claim_gate_refuses_a_missing_manifest(rendered_pair, tmp_path) -> None:  # type: ignore[no-untyped-def]
    gate, _ = rendered_pair
    with pytest.raises(gate.ClaimError, match="cityflow manifest"):
        gate.load_manifest(tmp_path / "absent.json")


def test_claim_gate_reaches_outside_the_claim_namespace(  # type: ignore[no-untyped-def]
    rendered_pair,
) -> None:
    """The window and the backend are stated by documents without a bespoke claim."""
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "Window opens {{ raw:window.start }} on {{ month:first_period }}, "
        "generated {{ iso:generated_at }}.\n",
        encoding="utf-8",
    )
    assert gate.main(["--write"]) == 0
    written = (root / "REPORT.md").read_text(encoding="utf-8")
    assert "2021-07-01" in written
    assert "July 2021" in written
    assert "2026-09-21T00:00:00+00:00" in written


def test_claim_gate_renders_every_table_the_documents_use(  # type: ignore[no-untyped-def]
    rendered_pair,
) -> None:
    """A table in a document is a set of figures and is rendered like one."""
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "{{ table:quarantine_by_rule }}\n\n{{ table:shipped }}\n\n{{ table:bench }}\n",
        encoding="utf-8",
    )
    assert gate.main(["--write"]) == 0
    written = (root / "REPORT.md").read_text(encoding="utf-8")
    assert "| `unknown_zone` | 1,234 | 0.5432 percent |" in written
    # A null row count renders as an empty cell, not as a zero.
    assert "| `agg_daily.parquet` |  | 2.50 |" in written
    assert "| pulse | KPI tiles | 12 | 1.50 | 2.50 | 3.50 | 3.51 | 8 | 75 percent |" in written


def test_claim_gate_refuses_a_table_with_no_column_definition(  # type: ignore[no-untyped-def]
    rendered_pair,
) -> None:
    """The heading and the precision of every column are decided once, in the gate."""
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "{{ table:backend }}\n", encoding="utf-8"
    )
    with pytest.raises(gate.ClaimError, match="No column definition"):
        gate.main(["--write"])


def test_claim_gate_refuses_to_table_something_that_is_not_a_list(  # type: ignore[no-untyped-def]
    rendered_pair,
) -> None:
    gate, root = rendered_pair
    (root / "docs" / "templates" / "REPORT.md").write_text(
        "{{ table:fct_trip_rows }}\n", encoding="utf-8"
    )
    with pytest.raises(gate.ClaimError, match="not a non empty list"):
        gate.main(["--write"])
