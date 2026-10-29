"""scripts/annotate_logs.py: the tail of each build log, as one annotation each.

The refresh workflow runs for hours on a runner whose logs are a download away.
This is what puts the evidence on the run page instead, so it gets the same
three checks every script here gets: what it keeps, what it refuses, and one
run as a real script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import annotate_logs
import pytest


def test_log_annotations_keep_the_tail_and_drop_colour_codes(tmp_path: Path) -> None:
    log = tmp_path / "04-dbt-build.log"
    lines = [f"\x1b[32mline {i}\x1b[0m" for i in range(200)]
    log.write_text("\n".join([*lines, "", ""]), encoding="utf-8")

    kept = annotate_logs.tail(log, 3)

    assert kept == ["line 197", "line 198", "line 199"]


def test_log_annotations_fit_inside_one_annotation(tmp_path: Path) -> None:
    log = tmp_path / "02-ingest.log"
    log.write_text("\n".join("x" * 1000 for _ in range(200)), encoding="utf-8")

    kept = annotate_logs.tail(log, 200)

    assert sum(len(line) + 1 for line in kept) <= annotate_logs.MAX_CHARACTERS
    assert kept[-1] == "x" * 1000


def test_the_log_script_annotates_each_log_and_skips_a_missing_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "06-reconcile.log").write_text(
        "412 metrics reconcile\nacross 9 checks\n", encoding="utf-8"
    )
    (tmp_path / "empty.log").write_text("", encoding="utf-8")

    code = annotate_logs.main(
        [
            "--level",
            "error",
            "--tail",
            "5",
            str(tmp_path / "06-reconcile.log"),
            str(tmp_path / "missing.log"),
            str(tmp_path / "empty.log"),
        ]
    )

    printed = capsys.readouterr().out.splitlines()
    assert code == 0
    assert printed == [
        "::error title=06-reconcile::412 metrics reconcile%0Aacross 9 checks",
        "::error title=empty::(empty)",
    ]


def test_the_log_script_runs_as_a_script(tmp_path: Path) -> None:
    """One subprocess run, so the __main__ guard keeps a test of its own."""
    log = tmp_path / "05-publish.log"
    log.write_text("Wrote manifest\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(Path(annotate_logs.__file__)), str(log)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "::notice title=05-publish::Wrote manifest"
