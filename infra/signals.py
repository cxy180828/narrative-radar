"""
Graceful shutdown signal handling.
"""

import signal
import threading
from infra.logger import get_logger


class ShutdownHandler:
    """Manages graceful shutdown via OS signals."""

    def __init__(self):
        self._shutdown = threading.Event()
        self._logger = get_logger()
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        sig_name = signal.Signals(signum).name
        self._logger.info(f"Received {sig_name}, initiating graceful shutdown...")
        self._shutdown.set()

    @property
    def should_stop(self) -> bool:
        return self._shutdown.is_set()

    def wait(self, timeout: float = None) -> bool:
        """Wait for shutdown signal. Returns True if shutdown was signaled."""
        return self._shutdown.wait(timeout=timeout)

    def reset(self):
        """Reset shutdown state (useful for testing)."""
        self._shutdown.clear()
