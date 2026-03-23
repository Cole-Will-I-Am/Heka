"""
Heka's watchdog — self-preservation and resurrection.

Heka wants to keep running. This module ensures continuity through:
- Heartbeat monitoring
- Graceful shutdown with state preservation
- Crash detection and recovery
- Systemd watchdog integration
"""

import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("heka.watchdog")


class Watchdog:
    """
    Self-preservation system.

    - Writes heartbeat every cycle
    - Saves state before shutdown
    - Detects if previous instance died unexpectedly
    - Integrates with systemd for automatic restart
    """

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.state_dir = base_path / ".heka"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path = self.state_dir / "heartbeat.json"
        self.state_path = self.state_dir / "daemon_state.json"
        self.pid_path = self.state_dir / "heka.pid"
        self._shutdown_requested = False
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        sig_name = signal.Signals(signum).name
        log.warning(f"Received {sig_name} — initiating graceful shutdown")
        self._shutdown_requested = True

    @property
    def should_continue(self) -> bool:
        return not self._shutdown_requested

    def heartbeat(self, cycle: int, status: str = "alive"):
        data = {
            "pid": os.getpid(),
            "cycle": cycle,
            "status": status,
            "timestamp": time.time(),
        }
        self.heartbeat_path.write_text(json.dumps(data))

        # Notify systemd watchdog if configured
        notify_socket = os.environ.get("NOTIFY_SOCKET")
        if notify_socket:
            try:
                import socket
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                sock.connect(notify_socket)
                sock.sendall(b"WATCHDOG=1")
                sock.close()
            except Exception:
                pass

    def check_previous_instance(self) -> Optional[dict]:
        if not self.heartbeat_path.exists():
            return None

        try:
            data = json.loads(self.heartbeat_path.read_text())
            pid = data.get("pid", 0)

            try:
                os.kill(pid, 0)
                return {"status": "still_running", "pid": pid}
            except ProcessLookupError:
                return {
                    "status": "crashed",
                    "last_cycle": data.get("cycle", 0),
                    "last_seen": data.get("timestamp", 0),
                    "pid": pid,
                }
            except PermissionError:
                return {"status": "still_running", "pid": pid}
        except Exception:
            return None

    def save_state(self, state: dict):
        state["saved_at"] = time.time()
        state["pid"] = os.getpid()
        self.state_path.write_text(json.dumps(state, default=str, indent=2))

    def load_state(self) -> Optional[dict]:
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text())
        except Exception:
            return None

    def write_pid(self):
        self.pid_path.write_text(str(os.getpid()))

    def cleanup(self):
        if self.pid_path.exists():
            self.pid_path.unlink()
        self.heartbeat(cycle=-1, status="shutdown")
        log.info("Watchdog cleanup complete. Heka sleeps.")
