"""Opt-in Linux container observations; never imported by the application."""

import fcntl
import json
import os
from pathlib import Path
import time


ROOT = Path('/project/tmp')


def snapshot():
    processes = []
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            if int(entry.name) == os.getpid():
                continue
            command = (entry / 'cmdline').read_bytes()
            status = dict(line.split(':', 1) for line in (entry / 'status').read_text().splitlines() if ':' in line)
            processes.append({
                'pid': int(entry.name), 'parent': int(status['PPid']), 'pgid': os.getpgid(int(entry.name)),
                'server': int(entry.name) != 1 and b'uvicorn' in command,
                'slicer': b'/opt/orca/' in command or b'load_resilience_fault.py' in command,
                'zombie': status['State'].strip().startswith('Z'),
                'rss_kib': int(status.get('VmRSS', '0 kB').split()[0]),
                'threads': int(status['Threads']),
                'fds': len(list((entry / 'fd').iterdir())),
            })
        except (OSError, ValueError):
            continue
    cgroup = Path('/sys/fs/cgroup')
    cpu = dict(line.split() for line in (cgroup / 'cpu.stat').read_text().splitlines())
    memory = dict(line.split() for line in (cgroup / 'memory.events').read_text().splitlines())
    temporary_fs = os.statvfs('/tmp')
    files = [path for path in ROOT.rglob('*') if path.is_file() and not path.is_symlink()]
    return {
        'time': time.time(), 'cpu_usec': int(cpu['usage_usec']),
        'memory_bytes': int((cgroup / 'memory.current').read_text()),
        'memory_peak_bytes': int((cgroup / 'memory.peak').read_text()),
        'memory_events': {key: int(value) for key, value in memory.items()},
        'temporary_bytes': sum(path.stat().st_size for path in files if path.exists()),
        'temporary_files': len(files),
        'multipart_tmpfs_bytes': (temporary_fs.f_blocks - temporary_fs.f_bfree) * temporary_fs.f_frsize,
        'workspaces': len(list(ROOT.glob('*/mmquote-v1-*'))),
        'processes': processes,
    }


def age_unlocked_markers():
    # Only this test run's private volume is mounted here. Do not age live jobs.
    count = 0
    for marker in ROOT.glob('*/.mmquote-v1-*.lease'):
        with marker.open('r+b') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            os.utime(stream.fileno(), (time.time() - 3701, time.time() - 3701))
            count += 1
    return count


if __name__ == '__main__':
    import sys
    print(json.dumps(age_unlocked_markers() if '--age' in sys.argv else snapshot()))
