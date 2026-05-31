import os
import subprocess

import pytest


pytestmark = pytest.mark.real_microsandbox
REAL_HOME = os.path.expanduser("~")


def _run_real_smoke(script, *args):
    if os.environ.get("HOOSEGOW_RUN_REAL_MICROSANDBOX") != "1":
        pytest.skip("set HOOSEGOW_RUN_REAL_MICROSANDBOX=1 to run real Microsandbox smokes")
    env = os.environ.copy()
    env["HOME"] = os.environ.get("HOOSEGOW_REAL_MICROSANDBOX_HOME", REAL_HOME)
    return subprocess.run(
        ["python3", script, *args],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
        env=env,
    )


def test_real_microsandbox_published_http_port_smoke():
    result = _run_real_smoke("scripts/microsandbox_port_smoke.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Microsandbox published HTTP port smoke passed" in result.stdout


def test_real_microsandbox_pty_controller_smoke():
    result = _run_real_smoke("scripts/pty_controller_microsandbox_smoke.py", "--verbose")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Microsandbox PTY controller HTTP smoke passed" in result.stdout
    assert "HOOSEGOW_HTTP_PTYD_SMOKE:/app" in result.stdout
