"""Real kernel-enforced memory bounds before trusted native child execution."""

import os
import sys

from app.self_build.git_process import limited_process


def test_native_memory_allocation_is_denied_by_operating_system(tmp_path):
    script = "try:\n bytearray(268435456)\nexcept MemoryError:\n print('bounded',flush=True)\nelse:\n raise SystemExit('unbounded')"
    with limited_process(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        env=dict(os.environ),
        authority_check=lambda: None,
        launch={},
        memory_bytes=134217728,
    ) as child:
        output, errors = child.communicate(timeout=15)
        assert child.returncode == 0, errors
        assert output.strip() == b"bounded"


def test_authority_failure_prevents_child_execution(tmp_path):
    marker = tmp_path / "must-not-exist"

    def denied():
        raise RuntimeError("authority revoked")

    import pytest

    with pytest.raises(RuntimeError, match="revoked"):
        with limited_process(
            [sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            cwd=tmp_path,
            env=dict(os.environ),
            authority_check=denied,
            launch={},
        ):
            raise AssertionError("child started")
    assert not marker.exists()
