import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "deploy" / "docker" / "entrypoint.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_entrypoint(tmp_path: Path, *, hosts_yml: str | None = None, extra_env: dict[str, str] | None = None):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    bin_dir = tmp_path / "bin"
    log_path = tmp_path / "commands.log"

    home.mkdir()
    workspace.mkdir()
    bin_dir.mkdir()

    if hosts_yml is not None:
        gh_dir = home / ".config" / "gh"
        gh_dir.mkdir(parents=True)
        (gh_dir / "hosts.yml").write_text(hosts_yml, encoding="utf-8")

    logger = """#!/usr/bin/env bash
set -euo pipefail
printf '%s|%s\\n' "$0" "$*" >> "${ENTRYPOINT_TEST_LOG}"
"""
    _write_executable(
        bin_dir / "git",
        logger + "\nexit 0\n",
    )
    _write_executable(
        bin_dir / "gh",
        logger
        + """
if [[ "${1:-}" == "auth" && "${2:-}" == "setup-git" ]]; then
  exit 1
fi
exit 0
""",
    )
    _write_executable(
        bin_dir / "python3",
        logger + "\nexit 0\n",
    )

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "BULLPEN_WORKSPACE": str(workspace),
            "ENTRYPOINT_TEST_LOG": str(log_path),
        }
    )
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, log_path.read_text(encoding="utf-8")


def test_entrypoint_installs_git_helper_fallback_for_copied_github_cli_auth(tmp_path):
    result, log_text = _run_entrypoint(
        tmp_path,
        hosts_yml="github.com:\n    oauth_token: test-token\n",
    )

    assert result.returncode == 0, result.stderr
    assert "gh auth setup-git failed; installing GitHub credential helper fallback" in result.stdout
    assert "git|config --global --unset-all credential.https://github.com.helper" in log_text
    assert "git|config --global --add credential.https://github.com.helper !gh auth git-credential" in log_text
    assert "git|config --global --unset-all credential.https://gist.github.com.helper" in log_text
    assert "git|config --global --add credential.https://gist.github.com.helper !gh auth git-credential" in log_text


def test_entrypoint_installs_git_helper_fallback_for_token_only_auth(tmp_path):
    result, log_text = _run_entrypoint(
        tmp_path,
        extra_env={"GH_TOKEN": "test-token"},
    )

    assert result.returncode == 0, result.stderr
    assert "git|config --global --unset-all credential.https://github.com.helper" in log_text
    assert "git|config --global --add credential.https://github.com.helper !gh auth git-credential" in log_text


def test_entrypoint_installs_git_helper_fallback_for_enterprise_host_auth(tmp_path):
    result, log_text = _run_entrypoint(
        tmp_path,
        hosts_yml="github.example.com:\n    oauth_token: test-token\n",
    )

    assert result.returncode == 0, result.stderr
    assert "git|config --global --unset-all credential.https://github.example.com.helper" in log_text
    assert "git|config --global --add credential.https://github.example.com.helper !gh auth git-credential" in log_text
