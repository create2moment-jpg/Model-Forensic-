"""Running ``mf serve`` instances: recorded at start, found by ``mf status``, stopped by ``mf stop``.

Each server writes ``<MF_HOME>/run/server-<port>.json`` (pid, host, port, url, start time, data dir) and removes it on
exit. Entries whose process is gone are cleaned up when listed. ``stop`` only signals a pid that is still a Model
Forensic server (checked against the process command line), sends SIGTERM (graceful: running prefetch tasks are kept
as resumable), waits, and escalates to SIGKILL only if asked.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any

from model_forensic.shared.paths import mf_home


def run_dir() -> Path:
    return mf_home() / "run"


def _file(port: int) -> Path:
    return run_dir() / f"server-{port}.json"


ANY_HOSTS = ("0.0.0.0", "::")  # noqa: S104 - compared against, never bound


def registered_url(host: str, port: int) -> str:
    return f"http://{'127.0.0.1' if host in ANY_HOSTS else host}:{port}"


def register(host: str, port: int, data_dir: Path, open_links: list[str] | None = None) -> Path:
    """Record a running server; ``open_links``: the links that open it from other machines (with its access token) —
    the file is readable by you only, so ``mf status`` can print them again."""
    run_dir().mkdir(parents=True, exist_ok=True)
    doc = {"pid": os.getpid(), "host": host, "port": port, "url": registered_url(host, port), "started_at": time.time(),
           "data_dir": str(data_dir), "open_links": open_links or []}  # fmt: skip
    p = _file(port)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(doc, indent=2))
    with contextlib.suppress(OSError):
        p.chmod(0o600)
    return p


def lan_addresses() -> list[str]:
    """This machine's addresses other machines can reach (no packet is sent: a UDP socket's route is only looked up)."""
    import socket

    out: list[str] = []
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        out.append(s.getsockname()[0])
    with contextlib.suppress(OSError):
        out += [a for a in socket.gethostbyname_ex(socket.gethostname())[2] if a not in out]
    host = socket.getfqdn()
    names = [a for a in out if not a.startswith("127.")]
    return ([host] if host and host != "localhost" and not host.startswith("127.") else []) + names


def ssh_tunnel_hint(port: int, env: dict[str, str] | None = None) -> str | None:
    """On an SSH session: the exact command to run on your laptop (this machine's user and address filled in)."""
    import getpass

    e = dict(os.environ) if env is None else env
    conn = (e.get("SSH_CONNECTION") or "").split()
    if len(conn) < 3:
        return None
    try:
        user = e.get("USER") or getpass.getuser()
    except Exception:
        user = "you"
    server = conn[2]
    host = f"[{server}]" if ":" in server else server
    return f"ssh -N -L {port}:127.0.0.1:{port} {user}@{host}"


def unregister(port: int) -> None:
    p = _file(port)
    with contextlib.suppress(OSError, ValueError):
        if json.loads(p.read_text()).get("pid") == os.getpid():
            p.unlink()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # an exited process its parent has not reaped yet (zombie) is not running
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("State:"):
                return "Z" not in line.split()[1]
    except OSError:
        pass
    return True


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _is_ours(pid: int) -> bool:
    cmd = _cmdline(pid)
    return not cmd or any(t in cmd for t in ("mf", "model_forensic", "model-forensic", "uvicorn"))


def memory_mb(pid: int) -> float | None:
    """Resident memory of a process (Linux /proc; psutil when installed)."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    try:
        import psutil  # type: ignore[import-untyped,unused-ignore]

        return float(round(psutil.Process(pid).memory_info().rss / 1024**2, 1))
    except Exception:
        return None


def port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1" if host in ANY_HOSTS else host, port)) == 0


def servers() -> list[dict[str, Any]]:
    """Recorded servers that are still running (stale records are removed)."""
    out: list[dict[str, Any]] = []
    if not run_dir().is_dir():
        return out
    for p in sorted(run_dir().glob("server-*.json")):
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            p.unlink(missing_ok=True)
            continue
        pid = int(doc.get("pid", 0))
        if not pid or not _alive(pid) or not _is_ours(pid):
            p.unlink(missing_ok=True)
            continue
        doc["uptime_s"] = round(time.time() - float(doc.get("started_at", time.time())))
        doc["memory_mb"] = memory_mb(pid)
        doc["answering"] = port_in_use(doc.get("host", "127.0.0.1"), int(doc["port"]))
        out.append(doc)
    return out


def stop(port: int | None = None, force: bool = False, timeout_s: float = 10.0) -> list[dict[str, Any]]:
    """Stop the recorded server on ``port`` (or every one). Returns what happened per server."""
    results = []
    for s in servers():
        if port is not None and int(s["port"]) != port:
            continue
        pid = int(s["pid"])
        if pid == os.getpid():
            continue
        os.kill(pid, signal.SIGTERM)
        end = time.time() + timeout_s
        while time.time() < end and _alive(pid):
            time.sleep(0.2)
        how = "stopped"
        if _alive(pid):
            if force:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                time.sleep(0.3)
                how = "killed" if not _alive(pid) else "still running"
            else:
                how = "still running (use --force)"
        if not _alive(pid):
            _file(int(s["port"])).unlink(missing_ok=True)
        results.append({**s, "result": how})
    return results
