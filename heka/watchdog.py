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

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.state_dir = self.base_path / ".heka"
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

    def heartbeat(self, cycle: int, status: str = "alive", meta: Optional[dict] = None):
        data = {
            "pid": os.getpid(),
            "cycle": cycle,
            "status": status,
            "timestamp": time.time(),
        }
        if meta:
            data["meta"] = meta
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

    def runtime_hazards(self, perception: dict) -> dict:
        health = perception.get("health", {}) if isinstance(perception, dict) else {}
        env = perception.get("environment", {}) if isinstance(perception, dict) else {}
        alerts = []
        severity = "normal"

        disk_percent = float(health.get("disk_percent", 0) or 0)
        disk_free_gb = float(health.get("disk_free_gb", 0) or 0)
        mem_percent = float(health.get("memory_percent", 0) or 0)
        mem_avail_mb = float(health.get("memory_available_mb", 0) or 0)

        if disk_percent >= 98 or disk_free_gb < 1.0:
            alerts.append("disk_critical")
            severity = "critical"
        elif disk_percent >= 90 or disk_free_gb < 3.0:
            alerts.append("disk_low")
            severity = "degraded" if severity != "critical" else severity

        if mem_percent >= 95 or mem_avail_mb < 256:
            alerts.append("memory_critical")
            severity = "critical"
        elif mem_percent >= 85 or mem_avail_mb < 768:
            alerts.append("memory_pressure")
            severity = "degraded" if severity != "critical" else severity

        if env.get("ollama_status") != "running":
            alerts.append("ollama_down")
            severity = "critical"

        try:
            soft_limit = os.sysconf("SC_OPEN_MAX")
            if soft_limit and soft_limit < 256:
                alerts.append("fd_limit_low")
                if severity == "normal":
                    severity = "degraded"
        except Exception:
            pass

        return {"severity": severity, "alerts": alerts}

    def cleanup(self):
        if self.pid_path.exists():
            self.pid_path.unlink()
        self.heartbeat(cycle=-1, status="shutdown")
        log.info("Watchdog cleanup complete. Heka sleeps.")
