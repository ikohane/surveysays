#!/usr/bin/env python3
"""
Run all tests: integration + UI smoke (if server running).

Usage:
    python3 admin_app/scripts/run_all_tests.py

The integration test always runs (no server needed).
The UI smoke test only runs if the server is detected on localhost:5055.
"""
from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    
    # Always run integration test (no server needed)
    print("=" * 60)
    print("Running integration test...")
    print("=" * 60)
    result = subprocess.run(
        [sys.executable, str(repo / "admin_app" / "scripts" / "integration_test.py")],
        cwd=str(repo),
        env={**__import__("os").environ, "PYTHONPATH": f"{repo}:{repo / 'qgen'}"},
    )
    if result.returncode != 0:
        print("\n❌ Integration test FAILED")
        sys.exit(1)
    print("✓ Integration test passed\n")
    
    # Run UI smoke test only if server is running
    server_url = "http://127.0.0.1:5055/"
    try:
        urllib.request.urlopen(server_url, timeout=2)
        server_running = True
    except Exception:
        server_running = False
    
    if server_running:
        print("=" * 60)
        print("Server detected, running UI smoke test...")
        print("=" * 60)
        result = subprocess.run(
            [sys.executable, str(repo / "admin_app" / "scripts" / "ui_smoke_test.py")],
            cwd=str(repo),
            env={**__import__("os").environ, "PYTHONPATH": f"{repo}:{repo / 'qgen'}"},
        )
        if result.returncode != 0:
            print("\n❌ UI smoke test FAILED")
            sys.exit(1)
        print("✓ UI smoke test passed\n")
    else:
        print("=" * 60)
        print(f"Server not running at {server_url}")
        print("Skipping UI smoke test (start server to include it)")
        print("=" * 60)
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    main()

