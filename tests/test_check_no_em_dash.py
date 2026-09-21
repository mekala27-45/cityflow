"""Tests for the em dash gate, including the cases that prove it is running.

Day 1's lesson, written down: four of that build's thirteen gate tests asserted
that the tree as committed passes, and every one of them would have passed while
two wrong figures were still in the documents. A gate needs a test that breaks
something on purpose, and it needs a test that the gate notices when it has been
handed nothing to look at.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "scripts" / "check_no_em_dash.py"


def run_gate(*arguments: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), *[str(a) for a in arguments]],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_file_passes(tmp_path: Path) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text("A sentence with a comma, a colon: and a range of 1 to 5.\n")
    result = run_gate(clean)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("character", "label"),
    [
        ("—", "em dash"),
        ("–", "en dash"),
        ("―", "horizontal bar"),
        ("−", "minus sign"),
    ],
)
def test_each_forbidden_character_is_caught(
    tmp_path: Path, character: str, label: str
) -> None:
    """The deliberate violation, once per character the gate claims to catch."""
    dirty = tmp_path / "dirty.py"
    dirty.write_text(f'"""A docstring with a {character} in it."""\n')
    result = run_gate(dirty)
    assert result.returncode == 1, f"{label} was not caught"
    assert label in result.stdout


def test_directory_argument_is_walked(tmp_path: Path) -> None:
    """A directory used to scan nothing and report a pass.

    This is the bug that let a whole package through the gate untouched while
    the gate printed success.
    """
    package = tmp_path / "package"
    (package / "nested").mkdir(parents=True)
    (package / "nested" / "module.py").write_text("x = 1  # a — hides here\n")
    result = run_gate(package)
    assert result.returncode == 1
    assert "em dash" in result.stdout


def test_empty_scan_is_not_a_pass(tmp_path: Path) -> None:
    """Zero scannable files must not exit 0."""
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "data.parquet").write_bytes(b"\x00\x01")
    result = run_gate(empty)
    assert result.returncode == 2, result.stdout + result.stderr


def test_box_drawing_characters_survive(tmp_path: Path) -> None:
    """The architecture diagrams are drawn with a different Unicode block."""
    diagram = tmp_path / "architecture.md"
    diagram.write_text(
        "```\n"
        "┌───┐\n"
        "│ a │\n"
        "└───┘\n"
        "```\n"
    )
    result = run_gate(diagram)
    assert result.returncode == 0, result.stdout


def test_binary_file_is_skipped_not_crashed(tmp_path: Path) -> None:
    fake = tmp_path / "image.md"
    fake.write_bytes(b"\xff\xfe\x00binary\x80\x81")
    result = run_gate(fake)
    assert result.returncode in (0, 2)


def test_the_repository_itself_is_clean() -> None:
    """The weakest test in this file, which is why it is last and not alone."""
    result = run_gate()
    assert result.returncode == 0, result.stdout + result.stderr
