#!/usr/bin/env python3
"""
start.py — NovaMind Agency Launcher

Starts both:
  1. FastAPI webhook receiver (port 8000) — for n8n / external triggers
  2. Task Poller — autonomous background worker

Usage:
    python start.py
    
Or run components separately:
    python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    python -m core.task_poller
"""
import subprocess
import sys
import os
import time

def main():
    env = os.environ.copy()
    
    print("=" * 60)
    print("  NovaMind Autonomous Agency")
    print("=" * 60)
    print()
    
    # Start FastAPI in background
    print("Starting FastAPI webhook server on :8000...")
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", "8000"],
        env=env,
    )
    time.sleep(2)   # Let FastAPI bind
    
    try:
        # FastAPI now runs the Task Poller internally as an async task
        print("NovaMind Agency is LIVE.")
        print("Monitoring tasks and broadcasting telemetry...")
        api_proc.wait()
    except KeyboardInterrupt:
        print("\n\nShutting down NovaMind Agency...")
    finally:
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()
        print("NovaMind Agency stopped cleanly.")


if __name__ == "__main__":
    main()
