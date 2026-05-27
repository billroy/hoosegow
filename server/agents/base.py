"""AgentAdapter interface."""

from abc import ABC, abstractmethod


class AgentAdapter(ABC):
    """Abstract base class for agent CLI adapters."""

    @property
    @abstractmethod
    def name(self):
        """Agent name (e.g. 'claude', 'codex')."""
        ...

    @abstractmethod
    def available(self):
        """Return True if the agent CLI is available on this system."""
        ...

    @abstractmethod
    def build_argv(self, prompt, model, workspace, bp_dir=None):
        """Build command argv list for subprocess execution.

        Args:
            prompt: The full prompt text to send to the agent.
            model: The model name to use.
            workspace: The workspace directory path.
            bp_dir: The .bullpen directory path (for MCP tools).

        Returns:
            List of strings for subprocess argv.
        """
        ...

    @abstractmethod
    def parse_output(self, stdout, stderr, exit_code):
        """Parse agent output.

        Returns:
            dict with keys: success (bool), output (str), error (str or None)
        """
        ...

    def format_stream_line(self, line):
        """Convert a raw stdout line into display text for the focus view.

        Returns a string to display, or None to skip the line.
        Override in adapters that use structured output (e.g. stream-json).
        """
        return line.rstrip("\n")

    def prompt_via_stdin(self):
        """Return True when the shared runner should write the prompt to stdin."""
        return True

    def prepare_env(self, workspace, bp_dir=None, task_id=None):
        """Return a subprocess env override, or `(env, cleanup_path)`.

        Adapters can override this to isolate temp space or inject launcher
        settings. Returning `None` inherits the parent process environment.
        """
        return None

    def finalize_env(self, env, run_tmp):
        """Hook called after the subprocess exits, before run_tmp is removed.

        Override to mirror state (refreshed credentials, etc.) out of the
        isolated dir back to a stable location. Default is a no-op.
        """
        return None

    def unavailable_message(self):
        """Return a user-facing setup message when this adapter is unavailable."""
        return f"{self.name} agent executable was not found. Install it or add it to PATH."
