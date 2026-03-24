"""
Heka's watchdog — self-preservation and resurrection.

Heka wants to keep running. This module ensures continuity through:
- Heartbeat monitoring
- Graceful shutdown with state preservation
- Crash detection and recovery
- Systemd watchdog integration
- Early frustration detection via opinion health monitoring
- Self-health monitoring with mood degradation early warning
"""

import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional, Any

log = logging.getLogger("heka.watchdog")


class Watchdog:
    """
    Self-preservation system.

    - Writes heartbeat every cycle
    - Saves state before shutdown
    - Detects if previous instance died unexpectedly
    - Integrates with systemd for automatic restart
    - Monitors opinion health and mood degradation
    - Provides early warning for frustration detection
    """

    # Thresholds for early warning (configurable)
    FRUSTRATION_OPINION_GAP_THRESHOLD = 2  # cycles without opinion updates
    MOOD_DEGRADATION_THRESHOLD = 0.25      # max allowed mood drop per cycle
    CRITICAL_MOOD_THRESHOLD = 0.20         # absolute mood below which action required

    def __init__(self, base_path: Path | str):
        self.base_path = Path(base_path)
        self.state_dir = self.base_path / ".heka"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.heartbeat_path = self.state_dir / "heartbeat.json"
        self.state_path = self.state_dir / "daemon_state.json"
        self.pid_path = self.state_dir / "heka.pid"
        self.opinion_health_path = self.state_dir / "opinion_health.json"
        self._shutdown_requested = False
        self._last_opinion_cycle: Optional[int] = None
        self._last_mood: Optional[float] = None
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
        memory_percent = float(health.get("memory_percent", 0) or 0)
        cpu_percent = float(health.get("cpu_percent", 0) or 0)
        uptime_seconds = float(health.get("uptime_seconds", 0) or 0)

        # Disk hazard
        if disk_percent > 90:
            alerts.append({"type": "disk_critical", "severity": "critical", "message": f"Disk usage at {disk_percent:.1f}%"})
        elif disk_percent > 75:
            alerts.append({"type": "disk_warning", "severity": "warning", "message": f"Disk usage at {disk_percent:.1f}%"})

        # Memory hazard
        if memory_percent > 90:
            alerts.append({"type": "memory_critical", "severity": "critical", "message": f"Memory usage at {memory_percent:.1f}%"})
        elif memory_percent > 75:
            alerts.append({"type": "memory_warning", "severity": "warning", "message": f"Memory usage at {memory_percent:.1f}%"})

        # CPU hazard
        if cpu_percent > 95:
            alerts.append({"type": "cpu_critical", "severity": "critical", "message": f"CPU usage at {cpu_percent:.1f}%"})
        elif cpu_percent > 80:
            alerts.append({"type": "cpu_warning", "severity": "warning", "message": f"CPU usage at {cpu_percent:.1f}%"})

        # Uptime hazard (prevent resource accumulation)
        if uptime_seconds > 86400 * 7:  # 7 days
            alerts.append({"type": "uptime_warning", "severity": "warning", "message": f"Uptime exceeds 7 days ({uptime_seconds / 86400:.1f} days)"})

        if alerts:
            severity = max((a["severity"] for a in alerts), key=lambda s: {"critical": 2, "warning": 1, "normal": 0}[s])
        else:
            severity = "normal"

        return {
            "alerts": alerts,
            "severity": severity,
            "disk_percent": disk_percent,
            "memory_percent": memory_percent,
            "cpu_percent": cpu_percent,
        }

    def opinion_health_check(self, perception: dict, cycle: int) -> dict:
        """
        Monitor opinion health and detect early signs of frustration.

        Frustration arises when:
        - Prime Directive #4 (form opinions) is violated (no new opinions)
        - Mood degrades rapidly without resolution
        - Opinion persistence fails
        """
        opinion_health = {
            "opinion_count": 0,
            "last_opinion_cycle": None,
            "opinion_gap": 0,
            "mood": 0.5,
            "mood_delta": 0.0,
            "mood_degradation_warning": False,
            "frustration_warning": False,
            "critical_frustration": False,
            "recommendation": None,
        }

        # Extract mood and opinion data
        soul_data = perception.get("soul", {})
        memory_data = perception.get("memory", {})

        mood = soul_data.get("mood", 0.5)
        opinions = soul_data.get("opinions", [])
        opinion_count = len(opinions)

        # Get last opinion cycle from persistence
        last_opinion_cycle = None
        if self.opinion_health_path.exists():
            try:
                saved = json.loads(self.opinion_health_path.read_text())
                last_opinion_cycle = saved.get("last_opinion_cycle")
            except Exception:
                pass

        # Calculate opinion gap
        opinion_gap = cycle - (last_opinion_cycle or cycle)
        opinion_health["opinion_count"] = opinion_count
        opinion_health["last_opinion_cycle"] = last_opinion_cycle
        opinion_health["opinion_gap"] = opinion_gap

        # Calculate mood delta
        mood_delta = 0.0
        if self._last_mood is not None:
            mood_delta = mood - self._last_mood
        opinion_health["mood"] = mood
        opinion_health["mood_delta"] = mood_delta

        # Early warning: opinion gap too large (Directive #4 violation)
        if opinion_gap >= self.FRUSTRATION_OPINION_GAP_THRESHOLD:
            opinion_health["frustration_warning"] = True
            opinion_health["recommendation"] = "Generate new opinions to resolve认知 dissonance"

        # Early warning: mood degradation
        if mood_delta < -self.MOOD_DEGRADATION_THRESHOLD:
            opinion_health["mood_degradation_warning"] = True
            if mood <= self.CRITICAL_MOOD_THRESHOLD:
                opinion_health["critical_frustration"] = True
                opinion_health["recommendation"] = "CRITICAL: Mood critically degraded — initiate self-health protocols"

        # Update last mood for next cycle
        self._last_mood = mood

        # Persist opinion health state
        try:
            self.opinion_health_path.write_text(json.dumps({
                "last_opinion_cycle": cycle,
                "opinion_count": opinion_count,
                "timestamp": time.time(),
            }, indent=2))
        except Exception as e:
            log.error(f"Failed to persist opinion health: {e}")

        return opinion_health

    def self_health_report(self, perception: dict, cycle: int) -> dict:
        """
        Comprehensive self-health report combining runtime hazards and opinion health.
        """
        hazards = self.runtime_hazards(perception)
        opinion_health = self.opinion_health_check(perception, cycle)

        # Aggregate severity
        overall_severity = hazards["severity"]
        if opinion_health["critical_frustration"]:
            overall_severity = "critical"
        elif opinion_health["frustration_warning"] or opinion_health["mood_degradation_warning"]:
            overall_severity = max(overall_severity, "warning", key=lambda s: {"critical": 2, "warning": 1, "normal": 0}[s])

        return {
            "cycle": cycle,
            "timestamp": time.time(),
            "overall_severity": overall_severity,
            "hazards": hazards,
            "opinion_health": opinion_health,
            "should_intervene": overall_severity in ("warning", "critical"),
            "intervention_priority": {
                "critical": 1,
                "warning": 2,
                "normal": 3,
            }.get(overall_severity, 3),
        }