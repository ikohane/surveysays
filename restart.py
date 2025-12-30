#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))
    except PermissionError as e:
        # Some sandboxes block process inspection tools like ps.
        return subprocess.CompletedProcess(cmd, 126, "", str(e))


def _list_listeners_on_port(port: int) -> list[int]:
    """
    Returns PIDs listening on localhost TCP port (best-effort).
    Uses lsof on macOS/Linux.
    """
    proc = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
    if proc.returncode != 0:
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            pass
    return sorted(set(pids))


def _find_admin_app_pids(repo_root: Path) -> list[int]:
    """
    Finds python processes that look like they are running the admin_app server.
    This is a fallback if the server isn't bound to the port yet.
    """
    proc = _run(["ps", "-ax", "-o", "pid=,command="])
    if proc.returncode != 0:
        return []

    needle1 = "python"
    needle2 = "admin_app.admin_app.app"
    needle3 = str((repo_root / "admin_app" / "admin_app" / "app.py").resolve())

    pids: list[int] = []
    for line in proc.stdout.splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_s, cmd = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if needle1 not in cmd:
            continue
        if (needle2 in cmd) or (needle3 in cmd):
            pids.append(pid)
    return sorted(set(pids))


def _terminate_pids(pids: list[int], *, timeout_sec: float = 3.0) -> None:
    if not pids:
        return

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
        if not alive:
            return
        time.sleep(0.1)

    # Escalate
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Kill prior local Admin server instances and restart the server.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5055")))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--db", default=os.environ.get("ADMIN_APP_DB", ""))
    parser.add_argument("--secret", default=os.environ.get("ADMIN_APP_SECRET", "dev"))
    parser.add_argument(
        "--background",
        action="store_true",
        help="Start the server in the background (prints PID and exits).",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent
    out_dir = repo_root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "server.log"
    pid_path = out_dir / "server.pid"

    # 1) Kill anything listening on the configured port
    pids_port = _list_listeners_on_port(args.port)
    # 2) Also kill any lingering admin_app processes (belt-and-suspenders)
    pids_admin = _find_admin_app_pids(repo_root)

    # Avoid killing our own process if ps matched us
    me = os.getpid()
    pids = sorted({pid for pid in (pids_port + pids_admin) if pid != me})

    if pids:
        print(f"Stopping existing server processes: {pids}")
        _terminate_pids(pids)
    else:
        print("No existing server processes found.")

    # Start server
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "qgen")
    env["ADMIN_APP_SECRET"] = args.secret
    env["PORT"] = str(args.port)
    env["HOST"] = args.host
    if args.db:
        env["ADMIN_APP_DB"] = args.db

    # IMPORTANT: Do NOT start via admin_app.admin_app.app's _main(), because it uses debug=True
    # which enables the Werkzeug reloader (multiple processes) and is flaky for "restart" scripts.
    # Instead, start a single-process server with debug/reloader disabled.
    cmd = [
        sys.executable,
        "-c",
        (
            "import os;"
            "from admin_app.admin_app.app import create_app;"
            "app=create_app();"
            "host=os.environ.get('HOST','127.0.0.1');"
            "port=int(os.environ.get('PORT','5055'));"
            "app.run(host=host, port=port, debug=False, use_reloader=False)"
        ),
    ]
    print("Starting server:")
    print("  " + " ".join(shlex.quote(c) for c in cmd))
    print(f"  HOST={env['HOST']} PORT={env['PORT']}")
    if "ADMIN_APP_DB" in env and env["ADMIN_APP_DB"]:
        print(f"  ADMIN_APP_DB={env['ADMIN_APP_DB']}")

    if args.background:
        env["PYTHONUNBUFFERED"] = "1"
        with log_path.open("ab") as logf:
            p = subprocess.Popen(cmd, env=env, cwd=str(repo_root), stdout=logf, stderr=logf)
        pid_path.write_text(str(p.pid), encoding="utf-8")

        # Health check: wait briefly for port bind
        time.sleep(0.6)
        listeners = _list_listeners_on_port(args.port)
        if not listeners:
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
                tail = "\n".join(tail)
            except Exception:
                pass
            print("Server failed to bind to port. Last log lines:\n" + (tail or "(no log output)"))
            try:
                os.kill(p.pid, signal.SIGTERM)
            except Exception:
                pass
            return 1

        print(f"Server started in background (pid={p.pid}). Logs: {log_path}")
        return 0

    # Foreground: inherit stdio
    p = subprocess.Popen(cmd, env=env, cwd=str(repo_root))
    try:
        return p.wait()
    except KeyboardInterrupt:
        print("\nCaught Ctrl-C, stopping server...")
        try:
            p.terminate()
        except Exception:
            pass
        return 130


if __name__ == "__main__":
    raise SystemExit(main())


