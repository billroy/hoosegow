import importlib.util
import asyncio
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy-sandbox.py"


def load_module():
    spec = importlib.util.spec_from_file_location("deploy_sandbox_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sb(monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "detect_supported_host", lambda: True)
    return module


def test_cli_requires_workspace_root(sb, monkeypatch):
    monkeypatch.chdir(ROOT)

    with pytest.raises(sb.DeployError, match="requires --workspace-root"):
        sb.config_from_args(["--admin-password", "pw", "--no-open"])


def test_cli_rejects_removed_workspace_options(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        sb.config_from_args(["--workspace", str(workspace), "--admin-password", "pw", "--no-open"])

    with pytest.raises(SystemExit):
        sb.config_from_args(["--install-bullpen-project", "--admin-password", "pw", "--no-open"])


def test_cli_accepts_noninteractive_options(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    home = tmp_path / "home"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--admin-password",
            "pw",
            "--sandbox-name",
            "testbox",
            "--bullpen-port",
            "8181",
            "--app-port",
            "3131",
            "--admin-user",
            "rootish",
            "--base",
            "test-base",
            "--sandbox-home",
            str(home),
            "--vcpus",
            "6",
            "--memory-mib",
            "8192",
            "--host-nofile",
            "16000",
            "--guest-nofile",
            "70000",
            "--network-max-connections",
            "16384",
            "--replace",
            "--no-open",
        ]
    )

    assert config.sandbox_name == "testbox"
    assert config.workspace == workspace.resolve()
    assert config.projects_root is None
    assert config.bullpen_port == 8181
    assert config.app_port == 3131
    assert config.admin_user == "rootish"
    assert config.admin_password == "pw"
    assert config.base == "test-base"
    assert config.sandbox_home == home.resolve()
    assert config.vcpus == 6
    assert config.memory_mib == 8192
    assert config.host_nofile == 16000
    assert config.guest_nofile == 70000
    assert config.network_max_connections == 16384
    assert config.provider_setup == "auto"
    assert config.replace is True
    assert config.open_browser is False


def test_cli_accepts_provider_setup_mode(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--admin-password",
            "pw",
            "--provider-setup",
            "require-existing",
            "--no-open",
        ]
    )

    assert config.provider_setup == "require-existing"


def test_cli_resource_options_default_to_larger_final_sandbox(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    config = sb.config_from_args(["--workspace-root", str(workspace), "--admin-password", "pw", "--no-open"])

    assert config.vcpus == 4
    assert config.memory_mib == 4096
    assert config.host_nofile == 12000
    assert config.guest_nofile == 65536
    assert config.network_max_connections == 8192


def test_cli_rejects_invalid_resource_options(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(sb.DeployError, match="Virtual CPUs must be at least 1"):
        sb.config_from_args(["--workspace-root", str(workspace), "--admin-password", "pw", "--vcpus", "0"])

    with pytest.raises(sb.DeployError, match="Memory MiB must be numeric"):
        sb.config_from_args(["--workspace-root", str(workspace), "--admin-password", "pw", "--memory-mib", "4G"])

    with pytest.raises(sb.DeployError, match="Network max connections must be at least 1"):
        sb.config_from_args(
            ["--workspace-root", str(workspace), "--admin-password", "pw", "--network-max-connections", "0"]
        )


def test_cli_auth_subcommand_does_not_require_admin_password(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(ROOT)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--no-open",
            "auth",
            "claude",
        ]
    )

    assert config.action == "auth"
    assert config.target == "claude"
    assert config.admin_password == ""


def test_cli_test_provider_subcommand_parses_target(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(ROOT)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--no-open",
            "test-provider",
            "git",
        ]
    )

    assert config.action == "test-provider"
    assert config.target == "git"


def test_cli_first_light_subcommand_parses_claude_without_admin_password(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(ROOT)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--no-open",
            "first-light",
            "claude",
        ]
    )

    assert config.action == "first-light"
    assert config.target == "claude"
    assert config.admin_password == ""


def test_cli_rejects_duplicate_ports(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(sb.DeployError, match="must be different"):
        sb.config_from_args(
            [
                "--workspace-root",
                str(workspace),
                "--admin-password",
                "pw",
                "--bullpen-port",
                "3000",
                "--app-port",
                "3000",
            ]
        )


def test_runtime_env_passes_microsandbox_label_to_server(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    sandbox_home = tmp_path / "sandbox-home"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--admin-password",
            "pw",
            "--sandbox-name",
            "bullpen-3",
            "--sandbox-home",
            str(sandbox_home),
            "--no-open",
        ]
    )
    sb.build_runtime_env(config)

    assert config.runtime_env["BULLPEN_DEPLOY_LABEL"] == "(Microsandbox:bullpen-3)"


def test_run_install_tui_processes_items_sequentially(sb, monkeypatch):
    order = []
    prompts = iter([True, False])

    async def auth_one(runtime, sandbox, config):
        order.append("auth-claude")

    async def verify_one(sandbox, config):
        order.append("verify-claude")
        if order.count("verify-claude") == 1:
            raise sb.DeployError("claude missing auth")

    async def auth_two(runtime, sandbox, config):
        order.append("auth-codex")

    async def verify_two(sandbox, config):
        order.append("verify-codex")
        if order.count("verify-codex") == 1:
            raise sb.DeployError("codex missing auth")

    async def auth_three(runtime, sandbox, config):
        order.append("auth-git")

    async def verify_three(sandbox, config):
        order.append("verify-git")
        raise sb.DeployError("git missing auth")

    monkeypatch.setattr(
        sb,
        "setup_items",
        lambda: [
            sb.SetupItem("claude", "Claude", auth_one, verify_one),
            sb.SetupItem("codex", "Codex", auth_two, verify_two),
            sb.SetupItem("git", "Git", auth_three, verify_three),
        ],
    )
    monkeypatch.setattr(sb.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sb, "prompt_yes_no", lambda _prompt, default=True: next(prompts))

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    summary = asyncio.run(sb.run_install_tui(object(), object(), config))

    assert order == [
        "verify-claude",
        "auth-claude",
        "verify-claude",
        "verify-codex",
        "auth-codex",
        "verify-codex",
        "verify-git",
    ]
    assert summary.selected_items == ["claude", "codex"]
    assert summary.skipped_items == ["git"]


def test_run_install_tui_skips_interactive_auth_when_provider_already_verifies(sb, monkeypatch):
    order = []

    async def auth_one(runtime, sandbox, config):
        order.append("auth-claude")

    async def verify_one(sandbox, config):
        order.append("verify-claude")

    monkeypatch.setattr(
        sb,
        "setup_items",
        lambda: [sb.SetupItem("claude", "Claude", auth_one, verify_one)],
    )
    monkeypatch.setattr(sb.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        sb,
        "prompt_yes_no",
        lambda _prompt, default=True: pytest.fail("should not prompt when sandbox auth verifies"),
    )

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    summary = asyncio.run(sb.run_install_tui(object(), object(), config))

    assert order == ["verify-claude"]
    assert summary.selected_items == ["claude"]
    assert summary.skipped_items == []


def test_run_provider_setup_skip_never_prompts(sb, monkeypatch):
    async def fail_install_tui(*_args):
        raise AssertionError("provider setup should not run in skip mode")

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        provider_setup="skip",
    )
    monkeypatch.setattr(sb, "run_install_tui", fail_install_tui)

    summary = asyncio.run(sb.run_provider_setup(object(), object(), config))

    assert summary.selected_items == []
    assert summary.skipped_items == []


def test_run_provider_setup_require_existing_verifies_all_items(sb, monkeypatch):
    calls = []

    async def auth_one(runtime, sandbox, config):
        calls.append("auth")

    async def verify_one(sandbox, config):
        calls.append("verify")

    monkeypatch.setattr(
        sb,
        "setup_items",
        lambda: [sb.SetupItem("claude", "Claude", auth_one, verify_one)],
    )
    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        provider_setup="require-existing",
    )

    summary = asyncio.run(sb.run_provider_setup(object(), object(), config))

    assert calls == ["verify"]
    assert summary.selected_items == ["claude"]


def test_auth_git_clears_stale_auth_and_requires_fresh_login(sb, monkeypatch):
    captured = {}
    calls = []

    async def fake_attach(runtime, sandbox, config, command, **kwargs):
        calls.append("attach")
        captured["command"] = command
        captured["kwargs"] = kwargs

    async def fake_clear(sandbox, config):
        calls.append("clear")

    monkeypatch.setattr(sb, "resolve_git_identity", lambda: ("Test User", "test@example.com"))
    monkeypatch.setattr(sb, "clear_git_auth", fake_clear)
    monkeypatch.setattr(sb, "attach_as_bullpen", fake_attach)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    asyncio.run(sb.auth_git(object(), object(), config))

    command = captured["command"]
    assert calls == ["clear", "attach"]
    assert "git config --global user.name 'Test User'" in command
    assert "git config --global user.email test@example.com" in command
    assert "gh auth login --hostname github.com --git-protocol https --web" in command
    assert "gh auth status --hostname github.com" in command
    assert "GitHub CLI already authenticated" not in command
    assert command.endswith("gh auth setup-git --hostname github.com")
    assert captured["kwargs"]["label"] == "authenticate GitHub CLI"


def test_clear_git_auth_moves_stale_github_cli_files(sb):
    commands = []

    class FakeSandbox:
        async def shell(self, command):
            commands.append(command)
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    asyncio.run(sb.clear_git_auth(FakeSandbox(), config))

    command = "\n".join(commands)
    assert "gh auth logout --hostname github.com" in command
    assert "/home/bullpen/.config/gh/hosts.yml" in command
    assert "/home/bullpen/.config/gh/config.yml" in command
    assert ".stale-$timestamp" in command


def test_deploy_applies_claude_network_mitigation_before_setup(sb, monkeypatch):
    calls = []
    sandbox = object()

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append("ensure")

        async def exists(self, name):
            calls.append(("exists", name))
            return False

        async def prepared_base_exists(self, base):
            calls.append(("base-exists", base))
            return True

        async def create(self, config):
            calls.append("create")
            return sandbox

    def async_step(name):
        async def _step(*_args):
            calls.append(name)
        return _step

    async def fake_install_tui(_runtime, _sandbox, _config):
        calls.append("install-tui")
        return sb.CredentialSummary(selected_items=["claude"])

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    monkeypatch.setattr(sb, "MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(sb, "ensure_host_ports_available", lambda _config: calls.append("ports"))
    monkeypatch.setattr(sb, "prepare_runtime_dirs", async_step("prepare"))
    monkeypatch.setattr(sb, "stage_static_assets", async_step("stage-static"))
    monkeypatch.setattr(sb, "disable_guest_ipv6_for_claude", async_step("disable-ipv6"))
    monkeypatch.setattr(sb, "verify_mount_access", async_step("mounts"))
    monkeypatch.setattr(sb, "configure_codex_cli", async_step("codex-cli"))
    monkeypatch.setattr(sb, "bootstrap_bullpen_credentials", async_step("bootstrap"))
    monkeypatch.setattr(sb, "start_bullpen", async_step("start"))
    monkeypatch.setattr(sb, "wait_for_health", lambda _port: calls.append("health"))
    monkeypatch.setattr(sb, "verify_admin_credentials", async_step("credentials"))
    monkeypatch.setattr(sb, "run_install_tui", fake_install_tui)
    monkeypatch.setattr(sb, "can_run_install_tui", lambda: True)
    monkeypatch.setattr(sb, "detach_sandbox", async_step("detach"))
    monkeypatch.setattr(sb, "verify_detached_sandbox", async_step("detached-health"))

    summary = asyncio.run(sb.deploy(config))

    assert summary.selected_items == ["claude"]
    assert calls.index("stage-static") < calls.index("disable-ipv6")
    assert calls.index("disable-ipv6") < calls.index("mounts")
    assert calls.index("disable-ipv6") < calls.index("install-tui")


def test_deploy_skips_install_setup_without_tty_and_detaches(sb, monkeypatch):
    calls = []
    sandbox = object()

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append("ensure")

        async def exists(self, name):
            calls.append(("exists", name))
            return False

        async def prepared_base_exists(self, base):
            calls.append(("base-exists", base))
            return True

        async def create(self, config):
            calls.append("create")
            return sandbox

    def async_step(name):
        async def _step(*_args):
            calls.append(name)
        return _step

    async def fail_install_tui(*_args):
        raise AssertionError("install setup should be skipped without a tty")

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    monkeypatch.setattr(sb, "MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(sb, "ensure_host_ports_available", lambda _config: calls.append("ports"))
    monkeypatch.setattr(sb, "prepare_runtime_dirs", async_step("prepare"))
    monkeypatch.setattr(sb, "stage_static_assets", async_step("stage-static"))
    monkeypatch.setattr(sb, "disable_guest_ipv6_for_claude", async_step("disable-ipv6"))
    monkeypatch.setattr(sb, "verify_mount_access", async_step("mounts"))
    monkeypatch.setattr(sb, "configure_codex_cli", async_step("codex-cli"))
    monkeypatch.setattr(sb, "bootstrap_bullpen_credentials", async_step("bootstrap"))
    monkeypatch.setattr(sb, "start_bullpen", async_step("start"))
    monkeypatch.setattr(sb, "wait_for_health", lambda _port: calls.append("health"))
    monkeypatch.setattr(sb, "verify_admin_credentials", async_step("credentials"))
    monkeypatch.setattr(sb, "run_install_tui", fail_install_tui)
    monkeypatch.setattr(sb, "can_run_install_tui", lambda: False)
    monkeypatch.setattr(sb, "detach_sandbox", async_step("detach"))
    monkeypatch.setattr(sb, "verify_detached_sandbox", async_step("detached-health"))

    summary = asyncio.run(sb.deploy(config))

    assert summary.selected_items == []
    assert "stage-static" in calls
    assert "detach" in calls
    assert "detached-health" in calls


def test_first_light_claude_runs_only_claude_gate_without_ports_or_bullpen(sb, monkeypatch):
    calls = []
    sandbox = object()

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append("ensure")

        async def exists(self, name):
            calls.append(("exists", name))
            return True

        async def prepared_base_exists(self, base):
            calls.append(("base-exists", base))
            return True

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

        async def create(self, config, *, expose_ports=True):
            calls.append(("create", expose_ports))
            return sandbox

    def async_step(name):
        async def _step(*_args):
            calls.append(name)
        return _step

    config = sb.DeployConfig(
        sandbox_name="bullpen-claude-first-light",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
        action="first-light",
        target="claude",
    )

    monkeypatch.setattr(sb, "MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(sb, "prepare_runtime_dirs", async_step("prepare"))
    monkeypatch.setattr(sb, "stage_static_assets", async_step("stage-static"))
    monkeypatch.setattr(sb, "disable_guest_ipv6_for_claude", async_step("disable-ipv6"))
    monkeypatch.setattr(sb, "verify_mount_access", async_step("mounts"))
    monkeypatch.setattr(sb, "auth_claude", async_step("auth-claude"))
    monkeypatch.setattr(sb, "verify_claude_credentials_file", async_step("credentials-file"))
    monkeypatch.setattr(sb, "verify_claude_auth", async_step("model-call"))
    monkeypatch.setattr(sb, "detach_sandbox", async_step("detach"))
    monkeypatch.setattr(sb, "ensure_host_ports_available", lambda _config: calls.append("ports"))
    monkeypatch.setattr(sb, "configure_codex_cli", async_step("codex-cli"))
    monkeypatch.setattr(sb, "bootstrap_bullpen_credentials", async_step("bootstrap"))
    monkeypatch.setattr(sb, "start_bullpen", async_step("start"))

    summary = asyncio.run(sb.run_first_light_command(config))

    assert summary.selected_items == ["claude"]
    assert ("stop", "bullpen-claude-first-light") in calls
    assert ("remove", "bullpen-claude-first-light") in calls
    assert ("create", False) in calls
    assert "ports" not in calls
    assert "codex-cli" not in calls
    assert "bootstrap" not in calls
    assert "start" not in calls
    assert calls.index("disable-ipv6") < calls.index("auth-claude")
    assert calls.index("auth-claude") < calls.index("credentials-file")
    assert calls.index("credentials-file") < calls.index("model-call")


def test_runtime_env_disables_nested_codex_sandbox_like_docker(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BULLPEN_CODEX_SANDBOX", raising=False)

    config = sb.config_from_args(
        [
            "--workspace-root",
            str(workspace),
            "--admin-password",
            "pw",
            "--no-open",
        ]
    )
    sb.build_runtime_env(config)

    assert config.runtime_env["BULLPEN_CODEX_SANDBOX"] == "none"
    assert config.runtime_env["BULLPEN_CODEX_PATH"] == "/usr/local/bin/codex"
    assert "SSL_CERT_FILE" not in config.runtime_env
    assert "SSL_CERT_DIR" not in config.runtime_env
    assert "NODE_EXTRA_CA_CERTS" not in config.runtime_env


def test_claude_tls_env_prefix_exports_system_trust_paths(sb):
    prefix = sb.claude_tls_env_prefix()

    assert f"export SSL_CERT_FILE={sb.shlex.quote(sb.SYSTEM_CA_CERT_FILE)}" in prefix
    assert f"export SSL_CERT_DIR={sb.shlex.quote(sb.SYSTEM_CA_CERT_DIR)}" in prefix
    assert f"export NODE_EXTRA_CA_CERTS={sb.shlex.quote(sb.SYSTEM_CA_CERT_FILE)}" in prefix
    assert 'export BUN_OPTIONS="${BUN_OPTIONS:+$BUN_OPTIONS }--use-system-ca"' in prefix


def test_runtime_create_uses_expected_microsandbox_shape(sb, tmp_path, monkeypatch):
    calls = {}

    class FakeVolume:
        @staticmethod
        def bind(path, readonly=False):
            return {"path": path, "readonly": readonly}

    class FakeNetwork:
        @staticmethod
        def allow_all():
            return types.SimpleNamespace(policy="allow-all", max_connections=None)

    class FakeSandbox:
        @staticmethod
        def create(name, **kwargs):
            calls["name"] = name
            calls["kwargs"] = kwargs
            return types.SimpleNamespace()

    class FakeSnapshot:
        @staticmethod
        def get(name):
            return types.SimpleNamespace(name=name, path="/snapshots/bullpen-microsandbox-local")

    fake_module = types.SimpleNamespace(
        Sandbox=FakeSandbox,
        Snapshot=FakeSnapshot,
        Volume=FakeVolume,
        Network=FakeNetwork,
        is_installed=lambda: True,
    )
    monkeypatch.setitem(sys.modules, "microsandbox", fake_module)

    workspace = tmp_path / "project"
    sandbox_home = tmp_path / "home"
    workspace.mkdir()
    config = sb.DeployConfig(
        sandbox_name="testbox",
        workspace=workspace,
        bullpen_port=8081,
        app_port=3001,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=sandbox_home,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=tmp_path / "project-default",
        runtime_env={"BULLPEN_PORT": "8081"},
    )

    sb.build_runtime_env(config)
    monkeypatch.setattr(sb, "ensure_host_nofile", lambda target: (target, target))
    runtime = sb.MicrosandboxRuntime()
    asyncio.run(runtime.ensure_installed())
    asyncio.run(runtime.create(config))

    assert calls["name"] == "testbox"
    assert calls["kwargs"]["snapshot"] == "/snapshots/bullpen-microsandbox-local"
    assert "image" not in calls["kwargs"]
    assert calls["kwargs"]["replace"] is True
    assert calls["kwargs"]["detached"] is True
    assert calls["kwargs"]["cpus"] == 4
    assert calls["kwargs"]["memory"] == 4096
    assert "memory_mib" not in calls["kwargs"]
    assert calls["kwargs"]["ports"] == {8081: 8081, 3001: 3001}
    assert calls["kwargs"]["network"].policy == "allow-all"
    assert calls["kwargs"]["network"].max_connections == 8192
    assert calls["kwargs"]["volumes"]["/app"] == {"path": str(ROOT), "readonly": True}
    assert calls["kwargs"]["volumes"]["/workspace"] == {"path": str(workspace), "readonly": False}
    assert "/workspace/project" not in calls["kwargs"]["volumes"]
    assert calls["kwargs"]["volumes"]["/home/bullpen"] == {"path": str(sandbox_home), "readonly": False}
    assert "/home/bullpen/.codex" not in calls["kwargs"]["volumes"]
    assert calls["kwargs"]["env"] == {
        "HOME": "/home/bullpen",
        "USER": "bullpen",
        "LOGNAME": "bullpen",
    }
    assert config.runtime_env["BULLPEN_PROJECTS_ROOT"] == "/workspace"
    assert config.runtime_env["BULLPEN_START_WITHOUT_PROJECT"] == "1"
    assert "BULLPEN_WORKSPACE" not in config.runtime_env
    assert "BULLPEN_WORKSPACE_NAME" not in config.runtime_env
    assert config.runtime_env["BULLPEN_VENV"] == "/opt/bullpen-venv"
    assert config.runtime_env["BULLPEN_MICROSANDBOX_HOST_NOFILE"] == "12000"
    assert config.runtime_env["BULLPEN_MICROSANDBOX_GUEST_NOFILE"] == "65536"
    assert config.runtime_env["BULLPEN_MICROSANDBOX_MAX_CONNECTIONS"] == "8192"


def test_host_port_preflight_reports_occupied_ports(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=workspace,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=tmp_path / "home",
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=tmp_path / "project-default",
    )
    monkeypatch.setattr(sb, "host_port_in_use", lambda port: port == 8080)
    monkeypatch.setattr(sb, "host_port_owner", lambda port: f"COMMAND PID NAME\npython 123 *:{port}")

    with pytest.raises(sb.DeployError) as excinfo:
        sb.ensure_host_ports_available(config)

    assert "required host port(s) are occupied" in str(excinfo.value)
    assert "Port 8080 is already listening" in str(excinfo.value)
    assert "python 123" in str(excinfo.value)


def test_replace_existing_sandbox_stops_and_removes_before_port_check(sb, tmp_path, monkeypatch):
    calls = []

    class FakeRuntime:
        async def exists(self, name):
            calls.append(("exists", name))
            return True

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=tmp_path / "home",
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=tmp_path / "project-default",
    )
    monkeypatch.setattr(sb, "wait_for_host_ports_available", lambda cfg: calls.append(("wait_ports", cfg.sandbox_name)))

    asyncio.run(sb.replace_existing_sandbox(FakeRuntime(), config))

    assert calls == [
        ("exists", "bullpen"),
        ("stop", "bullpen"),
        ("remove", "bullpen"),
        ("wait_ports", "bullpen"),
    ]


def test_replace_existing_sandbox_ignores_missing_sandbox(sb, tmp_path, monkeypatch):
    calls = []

    class FakeRuntime:
        async def exists(self, name):
            calls.append(("exists", name))
            return False

        async def stop(self, name):
            calls.append(("stop", name))

        async def remove(self, name):
            calls.append(("remove", name))

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=tmp_path / "home",
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=tmp_path / "project-default",
    )
    monkeypatch.setattr(sb, "wait_for_host_ports_available", lambda _cfg: calls.append(("wait_ports",)))

    asyncio.run(sb.replace_existing_sandbox(FakeRuntime(), config))

    assert calls == [("exists", "bullpen")]


def test_detach_sandbox_requires_sdk_detach(sb):
    with pytest.raises(sb.DeployError, match="sandbox.detach"):
        asyncio.run(sb.detach_sandbox(types.SimpleNamespace()))


def test_detach_sandbox_calls_sdk_detach(sb):
    calls = []

    class FakeSandbox:
        async def detach(self):
            calls.append("detach")

    asyncio.run(sb.detach_sandbox(FakeSandbox()))

    assert calls == ["detach"]


def test_verify_detached_sandbox_requires_running_status(sb, monkeypatch):
    class FakeRuntime:
        async def status(self, _name):
            return "stopped"

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    monkeypatch.setattr(sb, "wait_for_health", lambda _port: None)

    with pytest.raises(sb.DeployError, match="not running after detach"):
        asyncio.run(sb.verify_detached_sandbox(FakeRuntime(), config))


def test_verify_detached_sandbox_checks_health_when_running(sb, monkeypatch):
    calls = []

    class FakeRuntime:
        async def status(self, _name):
            return "running"

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    monkeypatch.setattr(sb, "wait_for_health", lambda port: calls.append(port))

    asyncio.run(sb.verify_detached_sandbox(FakeRuntime(), config))

    assert calls == [8080]


def test_bullpen_start_and_verification_use_venv_python(sb):
    commands = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    asyncio.run(sb.prepare_runtime_dirs(FakeSandbox(), config))
    asyncio.run(sb.stage_static_assets(FakeSandbox(), config))
    asyncio.run(sb.bootstrap_bullpen_credentials(FakeSandbox(), config))
    asyncio.run(sb.start_bullpen(FakeSandbox(), config))
    asyncio.run(
        sb.verify_admin_credentials(
            FakeSandbox(),
            config,
        )
    )

    command_texts = [args[1] for _cmd, args in commands]
    prepare_command = command_texts[0]
    assert "useradd --uid" in prepare_command
    assert "BULLPEN_UID=" in prepare_command
    assert "Existing bullpen user has uid" in prepare_command
    assert "mkdir -p /workspace /home/bullpen/logs /home/bullpen/bin /home/bullpen/.codex /var/lib/bullpen" in prepare_command
    assert "bullpen soft nofile 65536" in prepare_command
    assert "bullpen hard nofile 65536" in prepare_command
    assert "chown bullpen:\"$group_name\" /home/bullpen/logs /home/bullpen/bin /home/bullpen/.codex" in prepare_command
    assert "chown bullpen:\"$group_name\" /workspace" not in prepare_command
    assert "chown -R bullpen:\"$group_name\" /var/lib/bullpen" in prepare_command
    assert "chown -R bullpen:\"$group_name\" /home/bullpen\n" not in prepare_command
    assert "test -w /home/bullpen" in prepare_command
    assert any('cp -a /app/static/. "$BULLPEN_STATIC_ROOT"/' in command for command in command_texts)
    assert any("BULLPEN_BOOTSTRAP_PASSWORD=pw" in command for command in command_texts)
    assert any("su -s /bin/bash bullpen -c" in command for command in command_texts)
    assert any("bullpen.py --bootstrap-credentials" in command for command in command_texts)
    start_command = next(command for command in command_texts if "nohup /opt/bullpen-venv/bin/python bullpen.py" in command)
    assert all(cmd == "bash" for cmd, _args in commands)
    assert "test -x /opt/bullpen-venv/bin/python" in start_command
    assert "command -v node >/dev/null" in start_command
    assert ": > /home/bullpen/logs/bullpen.log" in start_command
    assert ": > /home/bullpen/logs/bullpen-proxy.log" in start_command
    assert "runtime limits: host_nofile=${BULLPEN_MICROSANDBOX_HOST_NOFILE}" in start_command
    assert "--workspace /workspace" in start_command
    assert "--start-without-project" in start_command
    assert "--host 127.0.0.1" in start_command
    assert '--port "$BULLPEN_INTERNAL_PORT"' in start_command
    assert "node /app/deploy/microsandbox/bullpen-proxy.js" in start_command
    assert any("cd /app && /opt/bullpen-venv/bin/python -" in command for command in command_texts)


def test_verify_claude_auth_runs_minimal_claude_preflight(sb):
    commands = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    asyncio.run(sb.verify_claude_auth(FakeSandbox(), config))

    command_texts = [args[1] for _cmd, args in commands]
    assert any("net.ipv6.conf.eth0.disable_ipv6" in command for command in command_texts)
    assert any("/etc/sysctl.d/99-bullpen-claude-ipv4.conf" in command for command in command_texts)
    assert any("claude --print --output-format stream-json" in command for command in command_texts)
    assert not any("--model claude-sonnet-4-6" in command for command in command_texts)
    assert not any("test -s /home/bullpen/.claude/.credentials.json" in command for command in command_texts)
    assert any("Claude auth preflight failed inside Microsandbox" in command for command in command_texts)


def test_verify_claude_credentials_file_checks_persisted_oauth(sb):
    commands = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    asyncio.run(sb.verify_claude_credentials_file(FakeSandbox(), config))

    command_text = commands[0][1][1]
    assert "su -s /bin/bash bullpen -c" in command_text
    assert "/home/bullpen/.claude/.credentials.json" in command_text
    assert "accessToken" in command_text
    assert "refreshToken" in command_text


def test_auth_claude_disables_guest_ipv6_before_interactive_login(sb, monkeypatch):
    commands = []
    attached = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    class FakeRuntime:
        pass

    async def fake_attach(_runtime, _sandbox, _config, command, *, label):
        attached.append((command, label))

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)
    monkeypatch.setattr(sb, "attach_as_bullpen", fake_attach)

    asyncio.run(sb.auth_claude(FakeRuntime(), FakeSandbox(), config))

    assert "net.ipv6.conf.eth0.disable_ipv6" in commands[0][1][1]
    assert attached
    assert "claude auth login" in attached[0][0]
    assert attached[0][1] == "authenticate Claude"


def test_auth_codex_uses_device_auth(sb, monkeypatch):
    attached = []
    cleared = []

    class FakeRuntime:
        pass

    async def fake_attach(_runtime, _sandbox, _config, command, *, label, **kwargs):
        attached.append((command, label, kwargs))

    async def fake_clear(_sandbox, _config):
        cleared.append(True)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)
    monkeypatch.setattr(sb, "attach_as_bullpen", fake_attach)
    monkeypatch.setattr(sb, "clear_codex_auth", fake_clear)

    asyncio.run(sb.auth_codex(FakeRuntime(), object(), config))

    assert cleared == [True]
    assert attached[0][0] == '"$BULLPEN_CODEX_PATH" login --device-auth'
    assert attached[0][1] == "authenticate Codex"
    assert attached[0][2]["bridge_localhost_callback"] is False
    assert attached[0][2]["prefer_exec_stream"] is False
    assert "--device-auth" in attached[0][0]
    assert "BROWSER=echo" not in attached[0][0]


def test_localhost_auth_callback_delivery_runs_curl_inside_sandbox(sb):
    calls = []

    class FakeSandbox:
        def exec(self, cmd, args):
            calls.append((cmd, args))
            return types.SimpleNamespace(returncode=0, stdout_text="")

    url = "http://localhost:1455/auth/callback?code=abc&state=xyz"

    assert sb._is_localhost_auth_callback(url)
    message = sb._deliver_localhost_callback_to_sandbox(FakeSandbox(), url)

    assert message == "Delivered localhost auth callback inside the sandbox."
    assert calls[0][0] == "bash"
    assert "curl -fsS --max-time 10" in calls[0][1][1]
    assert "http://localhost:1455/auth/callback?code=abc&state=xyz" in calls[0][1][1]


def test_localhost_auth_callback_delivery_supports_async_exec(sb):
    calls = []

    class FakeSandbox:
        async def exec(self, cmd, args):
            calls.append((cmd, args))
            return types.SimpleNamespace(returncode=0, stdout_text="")

    url = "http://localhost:1455/auth/callback?code=abc&state=xyz"
    message = asyncio.run(sb._deliver_localhost_callback_to_sandbox_async(FakeSandbox(), url))

    assert message == "Delivered localhost auth callback inside the sandbox."
    assert calls[0][0] == "bash"
    assert "curl -fsS --max-time 10" in calls[0][1][1]


def test_localhost_auth_callback_rejects_non_callback_urls(sb):
    assert not sb._is_localhost_auth_callback("https://example.test/login")
    assert not sb._is_localhost_auth_callback("http://localhost:1455/auth/callback?state=xyz")
    assert not sb._is_localhost_auth_callback("http://localhost:1455/other?code=abc")


def test_run_auth_command_dispatches_to_selected_setup_item(sb, monkeypatch):
    calls = []

    class FakeRuntime:
        async def ensure_installed(self):
            calls.append("ensure")

        async def get(self, name):
            calls.append(("get", name))
            return types.SimpleNamespace(exec=lambda *_args, **_kwargs: None)

    async def fake_health(config):
        calls.append("health")

    async def fake_configure_codex_cli(sandbox, config):
        calls.append("configure-codex-cli")

    async def fake_auth(runtime, sandbox, config):
        calls.append(("auth", config.target))

    monkeypatch.setattr(sb, "MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(sb, "ensure_bullpen_healthy", fake_health)
    monkeypatch.setattr(sb, "configure_codex_cli", fake_configure_codex_cli)
    monkeypatch.setattr(
        sb,
        "get_setup_item",
        lambda key: sb.SetupItem(key, key.title(), fake_auth, lambda *_args, **_kwargs: None),
    )

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
        action="auth",
        target="codex",
    )

    asyncio.run(sb.run_auth_command(config))

    assert calls[:3] == ["ensure", ("get", "bullpen"), "health"]
    assert "configure-codex-cli" in calls
    assert ("auth", "codex") in calls


def test_run_test_provider_command_warns_but_continues_when_bullpen_unhealthy(sb, monkeypatch, capsys):
    calls = []
    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
        action="test-provider",
        target="claude",
    )

    class FakeRuntime:
        async def ensure_installed(self):
            return None

        async def get(self, name):
            return types.SimpleNamespace(exec=lambda *_args, **_kwargs: None)

    async def fake_health(_config):
        raise sb.DeployError("Bullpen health check failed for http://127.0.0.1:8080/health: boom")

    async def fake_verify(_sandbox, _config):
        calls.append("verify")

    monkeypatch.setattr(sb, "MicrosandboxRuntime", FakeRuntime)
    monkeypatch.setattr(sb, "ensure_bullpen_healthy", fake_health)
    monkeypatch.setattr(
        sb,
        "get_setup_item",
        lambda key: sb.SetupItem(key, key.title(), lambda *_args, **_kwargs: None, fake_verify),
    )

    asyncio.run(sb.run_test_provider_command(config))

    assert calls == ["verify"]
    assert "Continuing because provider auth/test commands do not require Bullpen HTTP" in capsys.readouterr().err


def test_verify_mount_access_runs_as_bullpen_and_checks_workspace_config(sb):
    commands = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    asyncio.run(sb.verify_mount_access(FakeSandbox(), config))

    assert len(commands) == 1
    probe_command = commands[0][1][1]
    assert "su -s /bin/bash bullpen -c" in probe_command
    assert "test -w /workspace" in probe_command
    assert "/workspace/bullpen" not in probe_command
    assert ".bullpen" not in probe_command
    assert "effective user" not in probe_command
    assert "workspace metadata" not in probe_command


def test_run_sandbox_shell_raises_on_execoutput_exit_code(sb):
    class FakeSandbox:
        def exec(self, cmd, args):
            return types.SimpleNamespace(exit_code=127, success=False, stdout_text="", stderr_text="missing command")

    with pytest.raises(sb.DeployError, match="missing command"):
        asyncio.run(sb.run_sandbox_shell(FakeSandbox(), "missing-command"))


def test_attach_as_bullpen_uses_attach_options_and_bullpen_user(sb):
    attached = []
    exec_commands = []

    class FakeSandbox:
        def attach(self, cmd, options):
            attached.append((cmd, options))
            return None

        def exec(self, cmd, args):
            exec_commands.append((cmd, args))
            command = args[1]
            if "cat /tmp/bullpen-attach-status-" in command:
                return types.SimpleNamespace(returncode=0, stdout_text="0\n")
            return types.SimpleNamespace(returncode=0, stdout_text="")

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    class FakeRuntime:
        class AttachOptions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sb.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sb.sys.stdout, "isatty", lambda: True)
    try:
        asyncio.run(sb.attach_as_bullpen(FakeRuntime(), FakeSandbox(), config, "claude auth login"))
    finally:
        monkeypatch.undo()

    assert attached
    cmd, options = attached[0]
    assert cmd == "bash"
    assert options.kwargs["user"] == "bullpen"
    assert options.kwargs["args"][0] == "-lc"
    assert "claude auth login" in options.kwargs["args"][1]
    assert any("cat /tmp/bullpen-attach-status-" in args[1] for _cmd, args in exec_commands)


def test_attach_as_bullpen_uses_exec_stream_when_attach_is_unavailable(sb, monkeypatch):
    attached = []
    opened = []
    exec_commands = []

    class FakeSink:
        def write(self, _data):
            return None

        def close(self):
            return None

    class FakeHandle:
        def __init__(self):
            self.events = [
                runtime.StdoutEvent(b"Opening browser at https://example.test/login\n"),
                runtime.ExitedEvent(0),
            ]

        def take_stdin(self):
            return FakeSink()

        def recv(self):
            if self.events:
                return self.events.pop(0)
            return None

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=True,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    class FakeExecOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeStdin:
        @classmethod
        def pipe(cls):
            return "PIPE"

    class FakeEvent:
        def __init__(self, data=None, code=None):
            self.data = data
            self.code = code

    class FakeStdoutEvent(FakeEvent):
        pass

    class FakeStderrEvent(FakeEvent):
        pass

    class FakeExitedEvent(FakeEvent):
        pass

    class FakeRuntime:
        ExecOptions = FakeExecOptions
        Stdin = FakeStdin
        StdoutEvent = FakeStdoutEvent
        StderrEvent = FakeStderrEvent
        ExitedEvent = FakeExitedEvent
        AttachOptions = None

    runtime = FakeRuntime()
    runtime.StdoutEvent = FakeStdoutEvent
    runtime.StderrEvent = FakeStderrEvent
    runtime.ExitedEvent = FakeExitedEvent

    class FakeSandbox:
        def exec_stream(self, cmd, options):
            attached.append((cmd, options))
            return FakeHandle()

        def exec(self, cmd, args):
            exec_commands.append((cmd, args))
            command = args[1]
            if "cat /tmp/bullpen-attach-status-" in command:
                return types.SimpleNamespace(returncode=0, stdout_text="0\n")
            return types.SimpleNamespace(returncode=0, stdout_text="")

    monkeypatch.setattr(sb, "open_browser", lambda url: opened.append(url))
    monkeypatch.setattr(sb.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sb.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sb.sys.stdin, "fileno", lambda: 0, raising=False)
    monkeypatch.setattr(sb.select, "select", lambda _r, _w, _x, _t: ([], [], []))
    monkeypatch.setattr(sb.termios, "tcgetattr", lambda _fd: [0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(sb.termios, "tcsetattr", lambda _fd, _when, _attrs: None)
    monkeypatch.setattr(sb.tty, "setcbreak", lambda _fd: None)

    # Rebind runtime event classes into the fake handle closure.
    class FakeHandle:
        def __init__(self):
            self.events = [
                runtime.StdoutEvent(b"Opening browser at https://example.test/login\n"),
                runtime.ExitedEvent(code=0),
            ]

        def take_stdin(self):
            return FakeSink()

        def recv(self):
            if self.events:
                return self.events.pop(0)
            return None

    class FakeSandbox:
        def exec_stream(self, cmd, options):
            attached.append((cmd, options))
            return FakeHandle()

        def exec(self, cmd, args):
            exec_commands.append((cmd, args))
            command = args[1]
            if "cat /tmp/bullpen-attach-status-" in command:
                return types.SimpleNamespace(returncode=0, stdout_text="0\n")
            return types.SimpleNamespace(returncode=0, stdout_text="")

    asyncio.run(sb.attach_as_bullpen(runtime, FakeSandbox(), config, "claude auth login"))

    assert attached
    assert attached[0][0] == "bash"
    assert attached[0][1].kwargs["tty"] is True
    assert attached[0][1].kwargs["stdin"] == "PIPE"
    assert opened == ["https://example.test/login"]
    assert any("cat /tmp/bullpen-attach-status-" in args[1] for _cmd, args in exec_commands)


def test_attach_as_bullpen_attach_raises_when_interactive_command_exits_nonzero(sb):
    class FakeSandbox:
        def attach(self, cmd, options):
            return None

        def exec(self, cmd, args):
            command = args[1]
            if "cat /tmp/bullpen-attach-status-" in command:
                return types.SimpleNamespace(returncode=0, stdout_text="1\n")
            return types.SimpleNamespace(returncode=0, stdout_text="")

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    class FakeRuntime:
        class AttachOptions:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sb.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sb.sys.stdout, "isatty", lambda: True)
    try:
        with pytest.raises(sb.DeployError, match="Sandbox interactive command failed: authenticate Claude"):
            asyncio.run(
                sb.attach_as_bullpen(
                    FakeRuntime(),
                    FakeSandbox(),
                    config,
                    "claude auth login",
                    label="authenticate Claude",
                )
            )
    finally:
        monkeypatch.undo()


def test_configure_codex_cli_uses_real_codex_with_file_auth_store(sb):
    commands = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    asyncio.run(sb.configure_codex_cli(FakeSandbox(), config))

    command = commands[0][1][1]
    assert "cat > /home/bullpen/bin/codex" not in command
    assert "rm -rf /var/lib/bullpen/codex-home /var/lib/bullpen/codex.lock" in command
    assert "CODEX_RUNTIME_HOME" not in command
    assert "LOCK_TIMEOUT_SECONDS" not in command
    assert "cp -a" not in command
    assert "mkdir -p /home/bullpen/.codex/tmp/arg0" in command
    assert "rm -rf /home/bullpen/.codex/tmp/arg0/codex-arg0*" in command
    assert 'cli_auth_credentials_store = "file"' in command
    assert 'grep -Eq "^[[:space:]]*cli_auth_credentials_store' in command
    assert 'real_codex="${BULLPEN_CODEX_PATH:-$(command -v codex)}"' in command
    assert 'test -x "$BULLPEN_CODEX_PATH"' in command
    assert 'chown bullpen:"$(id -gn bullpen)" /home/bullpen/.codex /home/bullpen/.codex/config.toml' in command
    assert 'chown -R bullpen:"$(id -gn bullpen)" /home/bullpen/.codex/tmp' in command
    assert 'chown -R bullpen:"$(id -gn bullpen)" /home/bullpen/.codex\n' not in command
    assert 'chown -R bullpen:"$(id -gn bullpen)" /home/bullpen\n' not in command
    assert "test -w /home/bullpen/.codex" in command


def test_print_success_uses_ipv4_loopback(sb, capsys):
    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    sb.print_success(config, sb.CredentialSummary())

    output = capsys.readouterr().out
    assert "UI:   http://127.0.0.1:8080" in output
    assert "App:  http://127.0.0.1:3000" in output


def test_verify_codex_auth_runs_codex_exec_with_nested_sandbox_disabled(sb):
    commands = []

    class FakeSandbox:
        def exec(self, cmd, args):
            commands.append((cmd, args))
            return types.SimpleNamespace(returncode=0)

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )
    sb.build_runtime_env(config)

    asyncio.run(sb.verify_codex_auth(FakeSandbox(), config))

    command = commands[0][1][1]
    assert "su -s /bin/bash bullpen -c" in command
    assert "cd /workspace" in command
    assert "test -s /home/bullpen/.codex/auth.json" in command
    assert "timeout 45s bash -lc" in command
    assert "HOME=/home/bullpen BULLPEN_CODEX_SANDBOX=none" in command
    assert '"$BULLPEN_CODEX_PATH" exec --dangerously-bypass-approvals-and-sandbox --json --skip-git-repo-check -' in command


def test_configured_sandbox_shell_redacts_secret_values(sb):
    class FakeSandbox:
        def exec(self, cmd, args):
            return types.SimpleNamespace(exit_code=1, success=False, stdout_text="", stderr_text="bad secret-value")

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="secret-value",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
        runtime_env={"BULLPEN_BOOTSTRAP_PASSWORD": "secret-value"},
    )

    with pytest.raises(sb.DeployError) as excinfo:
        asyncio.run(sb.run_configured_sandbox_shell(FakeSandbox(), config, "false", label="labeled failure"))

    assert "secret-value" not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)
    assert "Sandbox command failed: labeled failure" in str(excinfo.value)
    assert "export BULLPEN_BOOTSTRAP_PASSWORD" not in str(excinfo.value)


def test_microsandbox_prepare_cli_defaults_to_node_base(sb, tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.chdir(tmp_path)

    config = sb.config_from_args(["--prepare-base", "--source-dir", str(ROOT), "--no-open"])

    assert config.action == "prepare-base"
    assert config.base == "bullpen-microsandbox-local"
    assert config.source_image == "node:22-bookworm"
    assert config.prepare_source == ROOT
    assert config.prepare_base_policy == "auto"


def test_prepare_base_validates_codex_helper_before_and_after_snapshot(sb, monkeypatch):
    commands = []
    prepare_sandbox = object()
    validation_sandbox = object()

    class FakeRuntime:
        async def stop(self, name):
            commands.append(("stop", name))

        async def remove(self, name):
            commands.append(("remove", name))

        async def create_prepare_sandbox(self, name, image, source):
            commands.append(("create-prepare", name, image, source))
            return prepare_sandbox

        async def create_snapshot(self, name, base):
            commands.append(("snapshot", name, base))

        async def create_base_validation_sandbox(self, name, base, config):
            commands.append(("create-validation", name, base, config.base))
            return validation_sandbox

    async def fake_run_logged(sandbox, command, *, label):
        commands.append((label, sandbox, command))

    async def fake_stop_prepare(sandbox):
        commands.append(("stop-prepare", sandbox))

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
        prepare_source=ROOT,
    )

    monkeypatch.setattr(sb, "run_logged_sandbox_shell", fake_run_logged)
    monkeypatch.setattr(sb, "stop_prepare_sandbox", fake_stop_prepare)

    asyncio.run(sb.prepare_base(FakeRuntime(), config))

    install_command = next(entry[2] for entry in commands if entry[0] == "Installing OS packages")
    verify_command = next(entry[2] for entry in commands if entry[0] == "Verifying prepared base")
    snapshot_command = next(entry[2] for entry in commands if entry[0] == "Validating prepared base snapshot")
    assert "bubblewrap" in install_command
    assert "command -v bwrap >/dev/null" in verify_command
    assert 'createRequire("/usr/local/lib/node_modules/@openai/codex/bin/codex.js")' in verify_command
    assert 'require.resolve(`${packageName}/package.json`)' in verify_command
    assert "packageJsonStat.size <= 0" in verify_command
    assert "codex --version" in verify_command
    assert "\n            sync\n" in verify_command
    assert "command -v bwrap >/dev/null" in snapshot_command
    assert 'createRequire("/usr/local/lib/node_modules/@openai/codex/bin/codex.js")' in snapshot_command
    assert "codex --version" in snapshot_command
    assert commands.index(("snapshot", "bullpen-microsandbox-local-prepare", "bullpen-microsandbox-local")) < commands.index(
        ("create-validation", "bullpen-microsandbox-local-v", "bullpen-microsandbox-local", "bullpen-microsandbox-local")
    )


def test_ensure_prepared_base_auto_prepares_missing_base(sb, monkeypatch):
    calls = []
    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=True,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    class FakeRuntime:
        async def prepared_base_exists(self, base):
            calls.append(("exists", base))
            return False

    async def fake_prepare(runtime, cfg, *, force=True):
        calls.append(("prepare", cfg.base, force))

    monkeypatch.setattr(sb, "prepare_base", fake_prepare)

    asyncio.run(sb.ensure_prepared_base(FakeRuntime(), config))

    assert calls == [
        ("exists", "bullpen-microsandbox-local"),
        ("prepare", "bullpen-microsandbox-local", True),
    ]


def test_choose_replace_no_replace_errors_when_existing(sb, monkeypatch):
    class FakeRuntime:
        async def exists(self, name):
            return True

    config = sb.DeployConfig(
        sandbox_name="bullpen",
        workspace=ROOT,
        bullpen_port=8080,
        app_port=3000,
        admin_user="admin",
        admin_password="pw",
        base="bullpen-microsandbox-local",
        sandbox_home=ROOT,
        replace=False,
        open_browser=False,
        install_bullpen_project=False,
        root=ROOT,
        bullpen_source=ROOT,
        github_repo_url="https://example.test/repo.git",
        local_project_path_default=ROOT / "project",
    )

    with pytest.raises(sb.DeployError, match="already exists"):
        asyncio.run(sb.choose_replace(FakeRuntime(), config))
