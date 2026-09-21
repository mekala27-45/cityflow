"""Shared wiring for the test suite.

The gate scripts live in `scripts/`, which is deliberately not an importable
package: they are command line tools, not a library. The tests still import
them and call `main(argv)` in process rather than through `subprocess.run`,
because a gate exercised only through a subprocess is a gate coverage cannot
see, and an untested-looking gate is the first one somebody deletes.

One subprocess test per script remains, so the `__main__` guard and the exit
code plumbing stay covered by something that actually executes them.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
