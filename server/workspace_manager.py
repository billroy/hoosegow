"""Multi-workspace manager for concurrent project support."""

import json
import os
import shutil
import tempfile
import threading

from server.persistence import read_json, write_json


GLOBAL_DIR = os.path.abspath(os.path.expanduser(os.environ.get("TOADY_HOME", "~/.toady")))
REGISTRY_PATH = os.path.join(GLOBAL_DIR, "projects.json")
REGISTRY_VERSION = 1


def projects_root():
    """Return the active projects root, if this runtime constrains projects."""
    root = (os.environ.get("TOADY_PROJECTS_ROOT") or os.environ.get("BULLPEN_PROJECTS_ROOT") or "").strip()
    if not root:
        return None
    return os.path.realpath(os.path.abspath(root))


def resolve_project_path(path):
    """Resolve a user-entered project path.

    In constrained runtimes, relative inputs are project names/paths under
    TOADY_PROJECTS_ROOT. Absolute inputs are still accepted, then validated
    by ensure_within_projects_root().
    """
    raw_path = (path or "").strip()
    expanded_path = os.path.expanduser(raw_path)
    root = projects_root()
    if root and expanded_path and not os.path.isabs(expanded_path):
        expanded_path = os.path.join(root, expanded_path)
    return os.path.abspath(expanded_path)


def ensure_within_projects_root(path):
    """Resolve and validate a project path against TOADY_PROJECTS_ROOT."""
    real_path = os.path.realpath(resolve_project_path(path))
    root = projects_root()
    if not root:
        return real_path
    try:
        common = os.path.commonpath([root, real_path])
    except ValueError:
        common = None
    if common != root or real_path == root:
        raise ValueError(f"Project path must be inside {root}: {path}")
    return real_path


class WorkspaceState:
    """Runtime state for a single workspace."""

    def __init__(self, workspace_id, path, name):
        self.id = workspace_id
        self.path = path
        self.name = name
        self.bp_dir = os.path.join(path, ".bullpen")
        self.lock = threading.Lock()
        self.scheduler = None

    def to_dict(self, *, include_path=True):
        data = {"id": self.id, "name": self.name}
        if include_path:
            data["path"] = self.path
        return data


class WorkspaceManager:
    """Manages multiple concurrent workspace states."""

    def __init__(self, global_dir=None):
        self._workspaces = {}  # id -> WorkspaceState
        self._lock = threading.Lock()
        self._global_dir = global_dir or GLOBAL_DIR
        self._registry_path = os.path.join(self._global_dir, "projects.json")
        self._ensure_global_dir()
        self._registry = self._load_registry()

    # --- Registry I/O ---

    def _ensure_global_dir(self):
        os.makedirs(self._global_dir, exist_ok=True)

    @property
    def global_dir(self):
        """Return the global Toady directory this manager is using."""
        return self._global_dir

    def _load_registry(self):
        if not os.path.exists(self._registry_path):
            return []
        try:
            with open(self._registry_path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return self._try_load_backup()
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "projects" in data:
            v = data.get("version", 0)
            if v > REGISTRY_VERSION:
                raise RuntimeError(
                    f"projects.json version {v} is newer than supported "
                    f"({REGISTRY_VERSION}); refusing to overwrite"
                )
            return data["projects"]
        return []

    def _try_load_backup(self):
        bak = self._registry_path + ".bak"
        if not os.path.exists(bak):
            return []
        try:
            with open(bak, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if isinstance(data, dict) and "projects" in data:
            return data["projects"]
        if isinstance(data, list):
            return data
        return []

    def _save_registry(self):
        self._ensure_global_dir()
        envelope = {"version": REGISTRY_VERSION, "projects": self._registry}
        content = json.dumps(envelope, indent=2) + "\n"
        if os.path.exists(self._registry_path):
            bak = self._registry_path + ".bak"
            try:
                shutil.copy2(self._registry_path, bak)
            except OSError:
                pass
        dir_path = os.path.dirname(self._registry_path)
        fd, tmp = tempfile.mkstemp(dir=dir_path, prefix=".tmp_projects_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, self._registry_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # --- Project management ---

    def _find_by_path(self, path):
        """Find a registry entry by absolute path."""
        for entry in self._registry:
            if entry["path"] == path:
                return entry
        return None

    def _generate_id(self):
        import uuid
        return str(uuid.uuid4())

    def register_project(self, path, name=None):
        """Register a project path. Returns the workspace ID.

        If the path was previously registered, reuses its ID.
        Initializes the workspace and creates a WorkspaceState.
        """
        path = resolve_project_path(path)
        real_path = ensure_within_projects_root(path)
        if not os.path.isdir(real_path):
            raise ValueError(f"Not a directory: {path}")

        # Reject non-absolute paths or paths with ..
        if ".." in path.split(os.sep):
            raise ValueError(f"Invalid path: {path}")

        # Use resolved path to prevent symlink-based traversal
        path = real_path

        with self._lock:
            # Check if already registered
            entry = self._find_by_path(path)
            if entry:
                ws_id = entry["id"]
                # Update name if provided
                if name and name != entry["name"]:
                    entry["name"] = name
                    self._save_registry()
            else:
                ws_id = self._generate_id()
                if name is None:
                    name = os.path.basename(path)
                entry = {"id": ws_id, "path": path, "name": name}
                self._registry.append(entry)
                self._save_registry()

            # Initialize workspace if not already active
            if ws_id not in self._workspaces:
                from server.init import init_workspace

                bp_dir = init_workspace(path)
                ws = WorkspaceState(ws_id, path, entry["name"])
                self._workspaces[ws_id] = ws

            return ws_id

    def remove_project(self, workspace_id):
        """Remove a project from the registry and tear down its state.

        Does not delete .bullpen/ data on disk.
        """
        with self._lock:
            ws = self._workspaces.pop(workspace_id, None)
            if ws and ws.scheduler:
                ws.scheduler.stop()

            self._registry = [e for e in self._registry if e["id"] != workspace_id]
            self._save_registry()

    def get(self, workspace_id):
        """Get a WorkspaceState by ID. Returns None if not found."""
        return self._workspaces.get(workspace_id)

    def get_or_activate(self, workspace_id):
        """Get a WorkspaceState by ID, activating it from the registry if needed.

        Returns None only if the ID is not in the registry at all.
        """
        ws = self._workspaces.get(workspace_id)
        if ws is not None:
            return ws
        # Look up in registry and activate
        for entry in self._registry:
            if entry["id"] == workspace_id:
                path = entry["path"]
                if not os.path.isdir(path):
                    return None
                self.register_project(path, name=entry.get("name"))
                return self._workspaces.get(workspace_id)
        return None

    def get_bp_dir(self, workspace_id):
        """Get the .bullpen directory path for a workspace."""
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            raise KeyError(f"Unknown workspace: {workspace_id}")
        return ws.bp_dir

    def get_workspace_path(self, workspace_id):
        """Get the workspace directory path."""
        ws = self._workspaces.get(workspace_id)
        if ws is None:
            raise KeyError(f"Unknown workspace: {workspace_id}")
        return ws.path

    def all_workspaces(self):
        """Return list of all active WorkspaceState objects."""
        return list(self._workspaces.values())

    def all_ids(self):
        """Return list of all active workspace IDs."""
        return list(self._workspaces.keys())

    def list_projects(self, *, include_path=True, include_unavailable=True):
        """Return registry entries for all registered projects.

        Each entry includes an ``available`` flag indicating whether the
        project directory currently exists on disk.
        """
        out = []
        for e in self._registry:
            try:
                path = ensure_within_projects_root(e["path"])
                available = os.path.isdir(path)
            except ValueError:
                available = False
            if not include_unavailable and not available:
                continue
            entry = {"id": e["id"], "name": e["name"]}
            if include_path:
                entry["path"] = e["path"]
            entry["available"] = available
            out.append(entry)
        return out

    def list_visible_projects(self, *, include_path=True):
        """Return the user-facing project list for the current runtime."""
        hide_unavailable = os.environ.get("TOADY_HIDE_UNAVAILABLE_PROJECTS", os.environ.get("BULLPEN_HIDE_UNAVAILABLE_PROJECTS", "")) == "1"
        return self.list_projects(
            include_path=include_path,
            include_unavailable=not hide_unavailable,
        )

    @property
    def default_id(self):
        """Return the ID of the first registered workspace, or None."""
        if self._workspaces:
            return next(iter(self._workspaces))
        return None
