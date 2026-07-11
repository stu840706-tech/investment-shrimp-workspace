#!/usr/bin/env python3
"""Timeout wrapper for process_file -- child process, hard SIGKILL on hang.
Returns (child_stdout, None) on success, (None, err_text) on failure.
Child stdout carries process_file progress lines incl. the char count,
which batch_process logs and parses (v4 behavior restored)."""
import sys, subprocess
from pathlib import Path

TIMEOUT = 600

def run_with_timeout(file_path, timeout_sec):
    inner = Path(__file__).parent / "_process_file_inner.py"
    proc = subprocess.Popen(
        [sys.executable, str(inner), str(file_path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return None, "TIMEOUT after %ss (killed)" % timeout_sec
    if proc.returncode != 0:
        return None, (stderr or "") + ("\n--- partial stdout ---\n" + stdout if stdout else "")
    return stdout, None

if __name__ == "__main__":
    out, err = run_with_timeout(sys.argv[1], TIMEOUT)
    if err:
        sys.stderr.write(err)
        sys.exit(1)
    sys.stdout.write(out or "")
