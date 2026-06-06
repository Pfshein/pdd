"""Fake long-running agent for hang/kill-tree tests.

Spawns a child that sleeps, records the child's pid to argv[1], then sleeps
itself. Used to verify that run_process kills the WHOLE tree on timeout.
"""
import subprocess
import sys
import time


def main():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as fh:
            fh.write(str(child.pid))
    time.sleep(120)


if __name__ == "__main__":
    main()
