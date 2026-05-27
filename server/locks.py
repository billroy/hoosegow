"""Shared threading locks for serializing layout mutations."""

import threading

# Single-writer lock to serialize all layout read-modify-write sequences.
# Used by both socket event handlers (events.py) and background agent
# threads (workers.py) to prevent race conditions. Some synchronous worker
# paths complete while still inside a socket handler, so the same thread must
# be allowed to re-enter the lock during shared completion handling.
write_lock = threading.RLock()
