"""Unified Ghost (webghost) restart manager.

Single source of truth for restarting the Ghost web server on a machine.

Historically this project had several different, inconsistent restart
strategies scattered across ``orchestrate.py``, ``deployment.py``,
``rules.py`` and ``rules.yaml``. Some of them ran
``unstickystop webghost && stickystop webghost`` which actually leaves
Ghost *stopped* (``stickystop`` gracefully stops Ghost and prevents it
from auto-restarting), which was a common cause of deployment failures.

This module replaces all of them with one robust, well-tested sequence:

    1. Check Ghost status (``trident_run check webghost``).
    2. If it is not running, clear every sticky stop reason
       (``trident_run unstickystop webghost -r <reason>``).
    3. Restart Ghost (``trident_run restart webghost``).
    4. Wait until Ghost is actually accepting connections by polling for the
       ``Ports Stats`` line in ``/a/logs/cache.log``.

The manager is transport-agnostic: callers supply a ``run_command`` callable
with the signature ``(command: str, timeout: int) -> (exit_code, stdout, stderr)``
so the same logic works over any SSH mechanism (``SSHManager`` or a raw
``ssh`` subprocess).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, List, Tuple

# (command, timeout_seconds) -> (exit_code, stdout, stderr)
CommandRunner = Callable[[str, int], Tuple[int, str, str]]

DEFAULT_TRIDENT_RUN = "/a/sbin/trident_run"
DEFAULT_CACHE_LOG = "/a/logs/cache.log"
DEFAULT_SERVICE = "webghost"

# Matches "(stop reason: foo)" or "(stop reasons: foo, bar)"
_STOP_REASON_RE = re.compile(r"\(stop reasons?:\s*([^)]+)\)")


class GhostRestartManager:
    """Robustly restart Ghost on a single machine.

    One instance is bound to one machine via the ``run_command`` callable.
    Instances are cheap; create one per machine and call :meth:`restart`.
    """

    def __init__(
        self,
        run_command: CommandRunner,
        machine_label: str = "",
        *,
        trident_run: str = DEFAULT_TRIDENT_RUN,
        service: str = DEFAULT_SERVICE,
        cache_log_path: str = DEFAULT_CACHE_LOG,
        command_timeout: int = 60,
        status_timeout: int = 30,
        ready_timeout: int = 90,
        poll_interval: int = 2,
        logger: logging.Logger = None,
    ):
        """Create a restart manager for a single machine.

        Args:
            run_command: Callable that executes a shell command on the target
                machine and returns ``(exit_code, stdout, stderr)``.
            machine_label: Human-readable identifier for log messages.
            trident_run: Path to the ``trident_run`` control utility.
            service: Ghost service name (``webghost``).
            cache_log_path: Path to Ghost's cache log used for readiness checks.
            command_timeout: Per-command timeout for control commands.
            status_timeout: Timeout for the status check command.
            ready_timeout: Maximum time to wait for Ghost to accept connections.
            poll_interval: Seconds between readiness polls.
            logger: Logger to use. Must support ``info``/``warning``/``error``.
        """
        self.run_command = run_command
        self.label = machine_label or "machine"
        self.trident_run = trident_run
        self.service = service
        self.cache_log_path = cache_log_path
        self.command_timeout = command_timeout
        self.status_timeout = status_timeout
        self.ready_timeout = ready_timeout
        self.poll_interval = poll_interval
        self.logger = logger or logging.getLogger(__name__)

    def check_status(self) -> Tuple[bool, List[str]]:
        """Return ``(is_running, stop_reasons)`` for Ghost on this machine.

        ``stop_reasons`` is empty when Ghost is running and contains one or
        more reasons (defaulting to ``['unknown']``) when it is stopped.
        """
        cmd = f"{self.trident_run} check {self.service}"
        self.logger.info(f"Checking Ghost status on {self.label}")

        exit_code, stdout, stderr = self.run_command(cmd, self.status_timeout)

        # "webghost : Running [OK]"
        # "webghost : Not running [OK] (stop reasons: unknown)"
        is_running = "Running" in stdout
        if is_running:
            self.logger.info(f"Ghost is running on {self.label}")
            return True, []

        reasons: List[str] = []
        match = _STOP_REASON_RE.search(stdout)
        if match:
            reasons = [r.strip() for r in match.group(1).split(",") if r.strip()]
        if not reasons:
            reasons = ["unknown"]

        self.logger.info(
            f"Ghost is not running on {self.label}, stop reason(s): {', '.join(reasons)}"
        )
        return False, reasons

    def clear_stop_reasons(self, reasons: List[str]) -> None:
        """Run ``unstickystop`` for each sticky stop reason."""
        for reason in reasons:
            cmd = f"{self.trident_run} unstickystop {self.service} -r {reason}"
            self.logger.info(f"Clearing stop reason '{reason}' on {self.label}")

            exit_code, stdout, stderr = self.run_command(cmd, self.command_timeout)
            if exit_code != 0:
                self.logger.warning(
                    f"unstickystop for reason '{reason}' on {self.label} "
                    f"returned exit code {exit_code} (stderr: {stderr.strip()})"
                )

    def wait_until_ready(self) -> bool:
        """Poll the cache log until Ghost is accepting connections."""
        self.logger.info(
            f"Waiting for Ghost to be ready on {self.label} "
            f"(checking {self.cache_log_path})..."
        )

        tail_cmd = f"tail -n 50 {self.cache_log_path} | grep -i 'Ports Stats'"
        deadline = time.time() + self.ready_timeout

        while time.time() < deadline:
            exit_code, stdout, stderr = self.run_command(tail_cmd, 10)
            if exit_code == 0 and "Ports Stats" in stdout:
                self.logger.info(
                    f"✓ Ghost is ready on {self.label} - accepting connections"
                )
                return True
            time.sleep(self.poll_interval)

        self.logger.error(f"✗ Timeout waiting for Ghost to be ready on {self.label}")
        return False

    def restart(self) -> bool:
        """Run the full robust restart sequence. Returns True on success."""
        # Step 1 & 2: check status and clear any sticky stop reasons.
        is_running, reasons = self.check_status()
        if not is_running:
            self.clear_stop_reasons(reasons)

        # Step 3: restart Ghost.
        self.logger.info(f"Restarting Ghost on {self.label}")
        restart_cmd = f"{self.trident_run} restart {self.service}"
        exit_code, stdout, stderr = self.run_command(restart_cmd, self.command_timeout)

        if exit_code != 0:
            self.logger.error(
                f"✗ Ghost restart command failed on {self.label} "
                f"(exit code {exit_code}, stderr: {stderr.strip()})"
            )
            return False

        # Step 4: wait until Ghost is actually accepting connections.
        if self.wait_until_ready():
            self.logger.info(
                f"✓ Ghost successfully restarted and ready on {self.label}"
            )
            return True

        self.logger.error(
            f"✗ Ghost restart timed out on {self.label} - not accepting connections"
        )
        return False
