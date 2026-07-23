from __future__ import annotations

import subprocess
import sys


def test_global_tasks_dir_cli_override_is_a_path(tmp_path) -> None:
    (tmp_path / "BENCHMARK.yaml").write_text("name: test\nbase_images: {}\n")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "sforge.cli",
            "--tasks-dir",
            str(tmp_path),
            "list",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "test" in completed.stdout
