"""Synthetic CLI fault injection, selected explicitly by the opt-in harness."""

import os
import signal
import subprocess
import sys
import time


def main():
    if os.environ.get('MM008_FAULT_MODE') == 'error':
        raise SystemExit(7)

    # A non-responsive child in the slicer process group, with inherited workspace
    # locks. This is a simulated hung CLI, not a claim that real Orca hung.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], close_fds=False)
    time.sleep(120)


if __name__ == '__main__':
    main()
