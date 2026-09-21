"""Tests for the em dash gate, including the cases that prove it is running.

Day 1's lesson, written down: four of that build's thirteen gate tests asserted
that the tree as committed passes, and every one of them would have passed while
two wrong figures were still in the documents. A gate needs a test that breaks
something on purpose, and it needs a test that the gate notices when it has been
handed nothing to look at.

The gate is called in process, for the reason given in tests/test_gates.py: a
gate run through a subprocess reads as untested in the coverage report. One
subprocess test stays at the bottom, covering the entry point itself.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import check_no_em_dash
import pytest

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "scripts" / "check_no_em_dash.py"


@dataclass(frozen=True, slots=True)
class GateRun:
    returncode: int
    stdout: str
    stderr: str


def run_gate(capsys: pytest.CaptureFixture[str], *arguments: str | Path) -> GateRun:
    code = check_no_em_dash.main([str(a) for a in arguments])
    captured = capsys.readouterr()
    return GateRun(returncode=code, stdout=captured.out, stderr=captured.err)


def test_clean_file_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text("A sentence with a comma, a colon: and a range of 1 to 5.\n")
    result = run_gate(capsys, clean)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("code_point", "label"),
    [
        (0x2014, "em dash"),
        (0x2013, "en dash"),
        (0x2015, "horizontal bar"),
        (0x2212, "minus sign"),
    ],
)
def test_each_forbidden_character_is_caught(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code_point: int, label: str
) -> None:
    """The deliberate violation, once per character the gate claims to catch."""
    dirty = tmp_path / "dirty.py"
    dirty.write_text(f'"""A docstring with a {chr(code_point)} in it."""\n')
    result = run_gate(capsys, dirty)
    assert result.returncode == 1, f"{label} was not caught"
    assert label in result.stdout


def test_directory_argument_is_walked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A directory used to scan nothing and report a pass.

    This is the bug that let a whole package through the gate untouched while
    the gate printed success.
    """
    package = tmp_path / "package"
    (package / "nested").mkdir(parents=True)
    (package / "nested" / "module.py").write_text(f"x = 1  # a {chr(0x2014)} hides here\n")
    result = run_gate(capsys, package)
    assert result.returncode == 1
    assert "em dash" in result.stdout


def test_empty_scan_is_not_a_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Zero scannable files must not exit 0."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "data.parquet").write_bytes(b"\x00\x01")
    result = run_gate(capsys, empty)
    assert result.returncode == 2, result.stdout + result.stderr


def test_a_path_that_matches_nothing_is_not_a_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An empty directory argument expands to no files at all."""
    empty = tmp_path / "nothing"
    empty.mkdir()
    result = run_gate(capsys, empty)
    assert result.returncode == 2
    assert "No files matched" in result.stderr


def test_box_drawing_characters_survive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The architecture diagrams are drawn with a different Unicode block."""
    diagram = tmp_path / "architecture.md"
    box = "".join(chr(c) for c in (0x250C, 0x2500, 0x2500, 0x2500, 0x2510))
    side = chr(0x2502)
    diagram.write_text(f"```\n{box}\n{side} a {side}\n{box}\n```\n")
    result = run_gate(capsys, diagram)
    assert result.returncode == 0, result.stdout


def test_binary_file_is_skipped_not_crashed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = tmp_path / "image.md"
    fake.write_bytes(b"\xff\xfe\x00binary\x80\x81")
    result = run_gate(capsys, fake)
    assert result.returncode in (0, 2)


def test_a_skipped_directory_is_not_scanned(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Vendored trees are not ours to clean, and scanning them is how a gate
    becomes something people turn off."""
    vendored = tmp_path / "package" / "node_modules"
    vendored.mkdir(parents=True)
    (vendored / "vendor.js").write_text(f"// a {chr(0x2014)} from somebody else\n")
    (tmp_path / "package" / "ours.md").write_text("Clean, ours, and scanned.\n")
    result = run_gate(capsys, tmp_path / "package")
    assert result.returncode == 0, result.stdout
    assert "1 scanned files" in result.stdout


def test_the_repository_itself_is_clean(capsys: pytest.CaptureFixture[str]) -> None:
    """The weakest test in this file, which is why it is last and not alone."""
    result = run_gate(capsys)
    assert result.returncode == 0, result.stdout + result.stderr


def test_entry_point_runs_as_a_script(tmp_path: Path) -> None:
    """One subprocess run, so the __main__ guard keeps a test of its own."""
    clean = tmp_path / "clean.md"
    clean.write_text("Nothing forbidden here.\n")
    result = subprocess.run(
        [sys.executable, str(GATE), str(clean)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
