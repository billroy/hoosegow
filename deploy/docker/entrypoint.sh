#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${BULLPEN_WORKSPACE:-/workspace}"
HOST="${BULLPEN_HOST:-0.0.0.0}"
PORT="${BULLPEN_PORT:-8080}"
APP_PORT="${APP_PORT:-3000}"

configure_github_git_helper_for_host() {
  local host="$1"
  [[ -n "$host" ]] || return 0
  git config --global --unset-all "credential.https://${host}.helper" >/dev/null 2>&1 || true
  git config --global --add "credential.https://${host}.helper" "!gh auth git-credential" >/dev/null 2>&1 || true
}

collect_github_auth_hosts() {
  local gh_hosts_file="$HOME/.config/gh/hosts.yml"
  local hosts=""
  local host=""

  if [[ -n "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
    hosts="github.com"
  fi

  if [[ -f "$gh_hosts_file" ]]; then
    while IFS= read -r host; do
      [[ -n "$host" ]] || continue
      case "
${hosts}
" in
        *"
${host}
"*) ;;
        *)
          hosts="${hosts}${hosts:+
}${host}"
          ;;
      esac
    done < <(sed -n 's/^\([A-Za-z0-9._-][A-Za-z0-9._-]*\):.*/\1/p' "$gh_hosts_file")
  fi

  case "
${hosts}
" in
    *"
github.com
"*)
      case "
${hosts}
" in
        *"
gist.github.com
"*) ;;
        *)
          hosts="${hosts}${hosts:+
}gist.github.com"
          ;;
      esac
      ;;
  esac

  if [[ -n "$hosts" ]]; then
    printf '%s\n' "$hosts"
  fi
}

configure_github_git_auth() {
  local github_hosts=""
  local host=""

  command -v gh >/dev/null 2>&1 || return 0

  if [[ -z "${GH_TOKEN:-${GITHUB_TOKEN:-}}" && ! -f "$HOME/.config/gh/hosts.yml" ]]; then
    return 0
  fi

  github_hosts="$(collect_github_auth_hosts)"
  [[ -n "$github_hosts" ]] || return 0

  if ! gh auth setup-git >/dev/null 2>&1; then
    echo "gh auth setup-git failed; installing GitHub credential helper fallback"
  fi

  while IFS= read -r host; do
    [[ -n "$host" ]] || continue
    configure_github_git_helper_for_host "$host"
  done <<EOF
$github_hosts
EOF
}

mkdir -p "$WORKSPACE"

if [[ -f "$HOME/.gitconfig.host" && ! -f "$HOME/.gitconfig" ]]; then
  {
    printf '[include]\n'
    printf '\tpath = ~/.gitconfig.host\n'
  } > "$HOME/.gitconfig"
fi

git config --global --add safe.directory "$WORKSPACE" >/dev/null 2>&1 || true

if [[ -n "${GIT_AUTHOR_NAME:-}" ]]; then
  git config --global user.name "$GIT_AUTHOR_NAME" >/dev/null 2>&1 || true
elif [[ -n "${GIT_COMMITTER_NAME:-}" ]]; then
  git config --global user.name "$GIT_COMMITTER_NAME" >/dev/null 2>&1 || true
fi

if [[ -n "${GIT_AUTHOR_EMAIL:-}" ]]; then
  git config --global user.email "$GIT_AUTHOR_EMAIL" >/dev/null 2>&1 || true
elif [[ -n "${GIT_COMMITTER_EMAIL:-}" ]]; then
  git config --global user.email "$GIT_COMMITTER_EMAIL" >/dev/null 2>&1 || true
fi

configure_github_git_auth

if [[ ! -f "$HOME/.claude.json" && -d "$HOME/.claude/backups" ]]; then
  CLAUDE_BACKUP="$(find "$HOME/.claude/backups" -maxdepth 1 -type f -name '.claude.json.backup.*' | sort -r | head -n 1 || true)"
  if [[ -n "$CLAUDE_BACKUP" ]]; then
    echo "Restoring Claude config from backup: ${CLAUDE_BACKUP}"
    cp "$CLAUDE_BACKUP" "$HOME/.claude.json"
  fi
fi

if [[ -n "${BULLPEN_BOOTSTRAP_PASSWORD:-}" ]]; then
  if [[ -n "${BULLPEN_BOOTSTRAP_FORCE:-}" ]]; then
    echo "Bootstrapping Bullpen credentials (force enabled)"
  else
    echo "Bootstrapping Bullpen credentials (if none exist yet)"
  fi
  python3 bullpen.py --bootstrap-credentials
else
  echo "BULLPEN_BOOTSTRAP_PASSWORD not set; skipping credential bootstrap"
fi

echo "Starting Bullpen on ${HOST}:${PORT} (app port convention: ${APP_PORT})"
exec python3 bullpen.py \
  --workspace "$WORKSPACE" \
  --host "$HOST" \
  --port "$PORT" \
  --no-browser \
  "$@"
