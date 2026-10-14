#!/usr/bin/env python3
"""Convenience launcher script: runs the pytest suite and starts the Streamlit app.

Can be run with any Python environment without prior manual venv activation:
    python3 run.py
Pass --test-only to execute tests without launching the Streamlit server:
    python3 run.py --test-only
"""
from pathlib import Path
import subprocess
import sys

BB84_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BB84_DIR / ".venv" / "bin" / "python"


def main():
    """Run verification tests and start the Streamlit visualizer."""
    if not VENV_PYTHON.exists():
        print(f"Error: virtual environment not found at {VENV_PYTHON}")
        sys.exit(1)

    print("=== 1/2 Running automated tests (pytest -v) ===")
    subprocess.run([str(VENV_PYTHON), "-m", "pytest", "-v"], cwd=BB84_DIR, check=True)

    if "--test-only" in sys.argv:
        return

    print("\n=== 2/2 Starting Streamlit app (Ctrl+C to stop) ===")
    subprocess.run([str(VENV_PYTHON), "-m", "streamlit", "run", "app.py"], cwd=BB84_DIR, check=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
