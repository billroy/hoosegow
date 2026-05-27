"""Regression checks for the Docker deploy script."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_deploy_docker_installs_bullpen_project_from_github_with_flag():
    text = _read("deploy-docker.sh")
    assert 'BULLPEN_GITHUB_REPO_URL="${BULLPEN_GITHUB_REPO_URL:-https://github.com/billroy/bullpen.git}"' in text
    assert 'BULLPEN_DOCKER_BUILD_CONTEXT="${BULLPEN_DOCKER_BUILD_CONTEXT:-$BULLPEN_GITHUB_REPO_URL}"' in text
    assert "--install-bullpen-project" in text
    assert "INSTALL_BULLPEN_PROJECT=1" in text
    assert 'git clone --depth 1 "$BULLPEN_GITHUB_REPO_URL" "$target_path"' in text
    assert "resolve_script_dir() {" in text
    assert 'SCRIPT_DIR="$(resolve_script_dir)"' in text
    assert 'LOCAL_PROJECT_PATH_DEFAULT="$(dirname "$SCRIPT_DIR")/$(basename "$SCRIPT_DIR")-project"' in text
    assert 'elif [[ -n "$SCRIPT_DIR" && "$(abs_path "$PWD")" == "$SCRIPT_DIR" ]]; then' in text
    assert 'if [[ "$INSTALL_BULLPEN_PROJECT" -eq 1 ]]; then' in text
    assert 'install_bullpen_project_from_github "$LOCAL_PROJECT_PATH_DEFAULT"' in text
    assert "Running deploy-docker.sh from the Bullpen repo root." not in text
    assert "Enter the project Bullpen should work on so Docker does not mount Bullpen itself by default." not in text
    assert "Use --install-bullpen-project to clone Bullpen from GitHub into" not in text
    assert 'Project path to mount into /workspace (required): ' in text
    assert "Type . if you intentionally want to mount the Bullpen repo itself." in text
    assert "Add Bullpen as a project?" not in text


def test_deploy_docker_builds_image_from_github_by_default():
    text = _read("deploy-docker.sh")
    assert '-t "$IMAGE_NAME" \\\n    "$BULLPEN_DOCKER_BUILD_CONTEXT"' in text
    assert '-t "$IMAGE_NAME" .' not in text


def test_deploy_docker_hides_unavailable_projects_in_container():
    text = _read("deploy-docker.sh")
    assert '-e "BULLPEN_HIDE_UNAVAILABLE_PROJECTS=1"' in text


def test_deploy_docker_passes_container_name_label_to_server():
    text = _read("deploy-docker.sh")
    compose = _read("docker-compose.yml")
    assert '-e "BULLPEN_DEPLOY_LABEL=(Docker:${CONTAINER_NAME})"' in text
    assert 'BULLPEN_DEPLOY_LABEL: "(Docker:bullpen)"' in compose


def test_deploy_docker_syncs_github_cli_auth_into_persistent_home():
    text = _read("deploy-docker.sh")
    assert "sync_dir_if_exists()" in text
    assert 'sync_dir_if_exists "$HOME/.config/gh" "$DOCKER_HOME/.config/gh"' in text
    assert "ensure_github_cli_auth" in text
    assert 'env -u GH_CONFIG_DIR -u XDG_CONFIG_HOME HOME="$DOCKER_HOME" gh "$@"' in text
    assert "env -u GH_TOKEN -u GITHUB_TOKEN -u GH_CONFIG_DIR -u XDG_CONFIG_HOME gh" in text
    assert "github_hosts_has_oauth_token() " in text
    assert "if github_hosts_has_oauth_token && github_cli_logged_in; then" in text
    assert "host_github_gh auth token --hostname github.com" in text
    assert "gh auth login --hostname github.com --git-protocol https --with-token" not in text
    assert 'printf \'github.com:\\n\'' in text
    assert 'printf \'    oauth_token: %s\\n\' "$(yaml_single_quote "$token")"' in text
    assert "gh auth status --hostname github.com" in text
    assert "Copied host GitHub CLI token into Docker home" in text
    assert "Log in to GitHub CLI now for Docker git push and auto-PR?" not in text


def test_deploy_docker_does_not_launch_claude_browser_login():
    text = _read("deploy-docker.sh")
    assert "claude auth login" not in text
    assert "Log in to Claude Code now using the host browser?" not in text
    assert "Complete Claude Code login outside this deploy" in text


def test_deploy_docker_does_not_support_anthropic_api_key_claude_auth():
    text = _read("deploy-docker.sh")
    assert "ANTHROPIC_API_KEY" not in text
    assert "Anthropic API key" not in text


def test_docker_entrypoint_sets_up_git_for_copied_github_cli_auth():
    text = _read("deploy/docker/entrypoint.sh")
    assert 'local gh_hosts_file="$HOME/.config/gh/hosts.yml"' in text
    assert "gh auth setup-git" in text
    assert 'credential.https://${host}.helper' in text


def test_docker_compose_hides_unavailable_projects_in_container():
    text = _read("docker-compose.yml")
    assert 'BULLPEN_HIDE_UNAVAILABLE_PROJECTS: "1"' in text
