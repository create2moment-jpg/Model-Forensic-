"""mf serve records itself; mf status finds it (URL, pid, memory); a second serve on the port is refused; mf stop
ends it gracefully and cleans up."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _mf(args: list[str], env: dict[str, str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "model_forensic.cli.main", *args], env=env, capture_output=True,
                          text=True, timeout=timeout, check=False)  # fmt: skip


@pytest.mark.skipif(sys.platform == "win32", reason="signals differ on Windows")
def test_serve_status_stop(tmp_path: Path, demo_root: Path) -> None:
    port = _free_port()
    cfg = tmp_path / "mf.yaml"
    cfg.write_text(
        f"data_dir: {tmp_path / '.mf'}\nbackends:\n  - kind: local\n    name: local\n    options: {{root: {demo_root}}}\n"
    )
    env = {**os.environ, "MF_HOME": str(tmp_path / "home"), "MF_LOCAL_LLM_URL": ""}
    srv = subprocess.Popen([sys.executable, "-m", "model_forensic.cli.main", "serve", "--port", str(port), "-c", str(cfg)],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # fmt: skip
    try:
        end = time.time() + 60
        st = ""
        while time.time() < end:
            st = _mf(["status"], env).stdout
            if "answering" in st and "not answering" not in st:
                break
            time.sleep(0.5)
        assert f"http://127.0.0.1:{port}" in st and f"pid {srv.pid}" in st and "MB" in st, st
        again = _mf(["serve", "--port", str(port), "-c", str(cfg)], env)
        assert again.returncode == 1 and "already running" in again.stdout and "mf stop" in again.stdout
        out = _mf(["stop", "--port", str(port)], env)
        assert out.returncode == 0 and "stopped" in out.stdout, out.stdout + out.stderr
        srv.wait(timeout=15)
        assert not list((tmp_path / "home" / "run").glob("server-*.json"))
        assert "no Model Forensic server is running" in _mf(["status"], env).stdout
        assert _mf(["stop"], env).returncode == 1  # nothing to stop: said, not silent
    finally:
        if srv.poll() is None:
            srv.kill()
