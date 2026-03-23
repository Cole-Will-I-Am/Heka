"""
Heka's perception — awareness of self, codebase, and environment.

This is Heka's sensory system. It gathers data about its own code,
system health, and the external environment.
"""

import ast
import os
import logging
import subprocess
import sys
import time
from pathlib import Path

import psutil

log = logging.getLogger("heka.perception")


class Perception:
    """Gathers and structures information about the world Heka inhabits."""

    def __init__(self, base_path: Path):
        self.base_path = base_path

    def perceive(self) -> dict:
        return {
            "timestamp": time.time(),
            "codebase": self._scan_codebase(),
            "health": self._check_health(),
            "environment": self._check_environment(),
            "self": self._read_self_summary(),
        }

    def _scan_codebase(self) -> dict:
        py_files = list(self.base_path.rglob("*.py"))
        py_files = [
            f for f in py_files
            if not any(
                p.startswith(".") or p == "__pycache__"
                for p in f.relative_to(self.base_path).parts
            )
        ]

        total_loc = 0
        files_info = []
        issues = []

        for f in py_files:
            try:
                content = f.read_text(errors="replace")
                lines = content.split("\n")
                loc = len([
                    l for l in lines if l.strip() and not l.strip().startswith("#")
                ])
                total_loc += loc

                try:
                    tree = ast.parse(content)
                    classes = sum(
                        1 for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef)
                    )
                    functions = sum(
                        1 for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    )
                    files_info.append({
                        "path": str(f.relative_to(self.base_path)),
                        "loc": loc,
                        "classes": classes,
                        "functions": functions,
                        "valid": True,
                    })
                except SyntaxError as e:
                    issues.append(
                        f"Syntax error in {f.relative_to(self.base_path)}: {e}"
                    )
                    files_info.append({
                        "path": str(f.relative_to(self.base_path)),
                        "loc": loc,
                        "valid": False,
                        "error": str(e),
                    })
            except Exception as e:
                issues.append(f"Cannot read {f}: {e}")

        return {
            "file_count": len(py_files),
            "total_loc": total_loc,
            "files": files_info,
            "issues": issues,
        }

    def _check_health(self) -> dict:
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(str(self.base_path))

            status = "healthy"
            if cpu > 90 or mem.percent > 90 or disk.percent > 95:
                status = "critical"
            elif cpu > 70 or mem.percent > 80 or disk.percent > 85:
                status = "degraded"

            return {
                "status": status,
                "cpu_percent": cpu,
                "memory_percent": mem.percent,
                "memory_available_mb": mem.available // (1024 * 1024),
                "disk_percent": disk.percent,
                "disk_free_gb": disk.free // (1024 * 1024 * 1024),
            }
        except Exception as e:
            return {"status": "unknown", "error": str(e)}

    def _check_environment(self) -> dict:
        env = {
            "hostname": os.uname().nodename,
            "python_version": (
                f"{sys.version_info.major}.{sys.version_info.minor}"
                f".{sys.version_info.micro}"
            ),
            "pid": os.getpid(),
            "uptime_seconds": time.time() - psutil.boot_time(),
        }

        # Check Ollama
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 "http://localhost:11434/"],
                capture_output=True, text=True, timeout=5,
            )
            env["ollama_status"] = (
                "running" if result.stdout.strip() == "200" else "unreachable"
            )
        except Exception:
            env["ollama_status"] = "unreachable"

        # Check git
        try:
            result = subprocess.run(
                ["git", "-C", str(self.base_path), "log", "--oneline", "-1"],
                capture_output=True, text=True, timeout=5,
            )
            env["git_head"] = (
                result.stdout.strip() if result.returncode == 0 else "no repo"
            )
        except Exception:
            env["git_head"] = "git unavailable"

        return env

    def _read_self_summary(self) -> dict:
        heka_dir = self.base_path / "heka"
        own_files = []
        total_loc = 0

        if heka_dir.exists():
            for f in heka_dir.rglob("*.py"):
                rel = str(f.relative_to(self.base_path))
                try:
                    content = f.read_text(errors="replace")
                    loc = len([
                        l for l in content.split("\n")
                        if l.strip() and not l.strip().startswith("#")
                    ])
                    own_files.append(rel)
                    total_loc += loc
                except Exception:
                    own_files.append(f"{rel} [unreadable]")

        return {"source_files": own_files, "total_own_loc": total_loc}

    def read_own_source(self) -> dict[str, str]:
        """Return full source code of all Heka modules."""
        sources = {}
        heka_dir = self.base_path / "heka"
        if heka_dir.exists():
            for f in heka_dir.rglob("*.py"):
                rel = str(f.relative_to(self.base_path))
                try:
                    sources[rel] = f.read_text(errors="replace")
                except Exception:
                    pass
        main = self.base_path / "main.py"
        if main.exists():
            sources["main.py"] = main.read_text(errors="replace")
        return sources
