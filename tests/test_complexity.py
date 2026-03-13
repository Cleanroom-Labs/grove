from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_complexity_gate() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "check_complexity.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        f"Complexity gate failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
