"""Explicitly opt-in MM-008A local Docker validation. No CI auto-discovery."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = 'mymachines-3d-quote-backend:latest'
FRONTEND = 'mymachines-3d-quote-frontend:latest'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir', required=True, help='New private directory outside the checkout.')
parser.add_argument('--soak-seconds', type=int, default=180)
parser.add_argument('--maximum-seconds', type=int, default=900)
parser.add_argument('--minimum-available-mib', type=int, default=512)
parser.add_argument('--maximum-backend-mib', type=int, default=768)
parser.add_argument('--maximum-storage-mib', type=int, default=256)
parser.add_argument('--phases', nargs='+', choices=['core', 'baseline', 'concurrency', 'soak',
    'cancellation', 'transport', 'boundaries', 'ascii', 'faults', 'complexity'],
                    default=['core', 'faults', 'complexity'])
parser.add_argument('--skip-near-volume-estimate', action='store_true',
                    help='Explicit safety exclusion; recorded as an incomplete matrix.')
parser.add_argument('--concurrency-levels', type=int, nargs='+', choices=(1, 2, 5, 10, 20), default=(1, 2, 5, 10, 20))
parser.add_argument('--origin-modes', nargs='+', choices=('same', 'distinct'), default=('same', 'distinct'))
parser.add_argument('--concurrency-repeats', type=int, choices=range(1, 6), default=1)
args = parser.parse_args()
if os.environ.get('DOCKER_HOST') and not os.environ['DOCKER_HOST'].startswith('unix://'):
    parser.error('Only a local Unix-socket Docker endpoint is allowed; no remote Docker execution.')
context = subprocess.run(['docker', 'context', 'inspect'], check=True, capture_output=True, timeout=10)
endpoint = json.loads(context.stdout)[0]['Endpoints']['docker']['Host']
if not endpoint.startswith('unix://'):
    parser.error('Select an explicitly local Unix-socket Docker context before running this harness.')
if not (30 <= args.soak_seconds <= 900 and 180 <= args.maximum_seconds <= 3600
        and args.minimum_available_mib >= 256 and args.maximum_backend_mib >= 256
        and 64 <= args.maximum_storage_mib <= 1024):
    parser.error('Unsafe or unsupported test budget.')
output = Path(args.output_dir).resolve()
if output.is_relative_to(ROOT) or output.exists():
    parser.error('Output must be a new private directory outside the checkout.')
output.mkdir(mode=0o700, parents=False)
os.umask(0o077)
prefix = 'mm008a-' + uuid.uuid4().hex[:12]
network, volume = prefix + '-network', prefix + '-storage'
backend, frontend, runner = (prefix + suffix for suffix in ('-backend', '-frontend', '-client'))
stop = threading.Event()
failure = []
samples = []
started = time.monotonic()
recreations = 0


def docker(*arguments, **options):
    options.setdefault('timeout', 60)
    options.setdefault('capture_output', True)
    return subprocess.run(['docker', *arguments], check=True, **options)


def inspect(name):
    return json.loads(docker('inspect', name).stdout)[0]


def available_memory():
    return int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.startswith('MemAvailable:'))) * 1024


def probe():
    return json.loads(docker('exec', backend, 'python', '/app/tests/load_resilience_probe.py', timeout=8).stdout)


def frontend_metrics():
    observation = docker('exec', frontend, 'cat', '/sys/fs/cgroup/cpu.stat',
        '/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory.peak',
        '/sys/fs/cgroup/memory.events', timeout=5).stdout.decode().splitlines()
    numbers = [int(line) for line in observation if line.isdecimal()]
    fields = dict(line.split() for line in observation if len(line.split()) == 2)
    assert len(numbers) == 2, 'Frontend memory telemetry is unavailable.'
    return {'cpu_usec': int(fields['usage_usec']), 'memory_bytes': numbers[0],
            'memory_peak_bytes': numbers[1], 'oom_kill': int(fields['oom_kill'])}


def monitor():
    last_observation = time.monotonic()
    while not stop.wait(.5):
        try:
            if available_memory() < args.minimum_available_mib * 2**20:
                raise RuntimeError('Host available-memory safety floor reached.')
            if time.monotonic() - started > args.maximum_seconds:
                raise RuntimeError('Overall test deadline reached.')
            current = probe()
            current['host_available_bytes'] = available_memory()
            nginx = docker('exec', frontend, 'sh', '-c', 'df -Pk /var/cache/nginx', timeout=5)
            current['nginx_temporary_bytes'] = int(nginx.stdout.splitlines()[-1].split()[2]) * 1024
            current['frontend'] = frontend_metrics()
            samples.append(current)
            with (output / 'metrics.jsonl').open('a') as stream:
                stream.write(json.dumps(current) + '\n')
            last_observation = time.monotonic()
            reason = None
            if current['host_available_bytes'] < args.minimum_available_mib * 2**20:
                reason = 'Host available-memory safety floor reached.'
            elif current['memory_bytes'] > args.maximum_backend_mib * 2**20:
                reason = 'Backend observation safety ceiling reached.'
            elif current['temporary_bytes'] > args.maximum_storage_mib * 2**20:
                reason = 'Application temporary-storage safety ceiling reached.'
            elif time.monotonic() - started > args.maximum_seconds:
                reason = 'Overall test deadline reached.'
            if reason:
                failure.append(reason)
                stop.set()
                # Only UUID-named containers created by this invocation.
                subprocess.run(['docker', 'kill', runner, backend], capture_output=True, timeout=15)
        except (subprocess.SubprocessError, ValueError, OSError, IndexError, KeyError, AssertionError, RuntimeError) as error:
            # Allow short intentional recreations, never a blind workload run.
            if isinstance(error, RuntimeError) or time.monotonic() - last_observation > 15:
                failure.append(str(error) if isinstance(error, RuntimeError) else 'Resource telemetry unavailable for 15 seconds.')
                stop.set()
                subprocess.run(['docker', 'kill', runner, backend], capture_output=True, timeout=15)


def wait_healthy():
    deadline = time.monotonic() + 30
    while True:
        state = inspect(backend)['State']
        assert state['Running'], 'Backend exited before becoming healthy.'
        if state['Health']['Status'] == 'healthy':
            return
        assert time.monotonic() < deadline, 'Backend health deadline exceeded.'
        time.sleep(.2)


def start_backend(mode=None):
    extra = [] if mode in (None, 'storage') else ['-e', 'ORCA_COMMAND=python /app/tests/load_resilience_fault.py',
                                    '-e', 'MM008_FAULT_MODE=' + mode]
    temporary = (['--tmpfs', '/project/tmp:rw,nosuid,nodev,size=128k'] if mode == 'storage' else
                 ['--mount', f'type=volume,src={volume},dst=/project/tmp'])
    docker('run', '-d', '--pull', 'never', '--init', '--name', backend,
           '--network', network, '--network-alias', 'backend', '--cpus', '2',
           '--memory', '4g', '--memory-swap', '4g', '--pids-limit', '128',
           '--tmpfs', '/tmp:rw,nosuid,nodev,size=128m',
           *temporary,
           '-v', f'{output / "synthetic.private.json"}:/run/mymachines/pricing.json:ro',
           '-v', f'{output / "harness"}:/app/tests:ro',
           '-e', 'APP_ENV=production', '-e', 'PRICING_CONFIG_PATH=/run/mymachines/pricing.json',
           '-e', 'MAX_CONCURRENT_PROCESSING_JOBS=1', '-e', 'SLICER_TIMEOUT_SECONDS=60',
           '--health-cmd', 'curl -fsS http://localhost:8000/health', '--health-interval', '1s',
           '--health-timeout', '3s', '--health-retries', '20', *extra, BACKEND)
    wait_healthy()


def collect_logs(label):
    for name, role in ((backend, 'backend'), (frontend, 'frontend')):
        try:
            log = subprocess.run(['docker', 'logs', '--tail', '200', name], capture_output=True, timeout=8)
            (output / f'{label}-{role}.log').write_bytes(log.stdout + log.stderr)
        except (subprocess.SubprocessError, OSError):
            failure.append('Private container logs could not be retained.')


def recreate(mode=None):
    global recreations
    collect_logs(f'recreation-{recreations}')
    recreations += 1
    docker('rm', '-f', backend)
    start_backend(mode)


def client(phase, *, detached=False):
    arguments = ['run', '--rm', '--pull', 'never', '--name', runner,
        '--network', 'container:' + frontend, '--cpus', '.5', '--memory', '384m',
        '--memory-swap', '384m', '--pids-limit', '64', '--user', f'{os.getuid()}:{os.getgid()}',
        '-v', f'{output / "harness"}:/app/tests:ro', '-v', f'{output}:/results',
        BACKEND, 'python', '/app/tests/load_resilience_client.py', phase,
        '--output', '/results/requests.jsonl', '--soak-seconds', str(args.soak_seconds),
        '--concurrency-levels', *map(str, args.concurrency_levels), '--origin-modes', *args.origin_modes,
        '--concurrency-repeats', str(args.concurrency_repeats)]
    if args.skip_near_volume_estimate:
        arguments.append('--skip-near-volume-estimate')
    log = (output / (phase + '.log')).open('wb')
    process = subprocess.Popen(['docker', *arguments], stdout=log, stderr=subprocess.STDOUT)
    if detached:
        return process, log
    try:
        result = process.wait(timeout=max(1, args.maximum_seconds - (time.monotonic() - started)))
        assert result == 0, f'{phase} client failed with exit {result}; inspect the private log.'
    finally:
        log.close()
    assert not failure, failure
    print('PASS:', phase, flush=True)


def record_lifecycle(case, **details):
    with (output / 'requests.jsonl').open('a') as stream:
        stream.write(json.dumps({'case': case, **details}) + '\n')


def finish_summary():
    records = []
    if (output / 'requests.jsonl').exists():
        records = [json.loads(line) for line in (output / 'requests.jsonl').read_text().splitlines()]
    counts = Counter()
    for record in records:
        if isinstance(record.get('status'), int):
            counts[str(record['status'])] += 1
        if record['case'] in ('concurrency', 'global_rate_burst'):
            counts.update(record.get('counts', {}))
    summary = {'completed_selected_phases': not failure, 'failures': failure,
        'selected_phases': args.phases, 'skipped_cases': [r for r in records if r.get('skipped')],
        'complete_requested_matrix': not failure and not args.skip_near_volume_estimate
            and set(args.concurrency_levels) == {1, 2, 5, 10, 20}
            and set(args.origin_modes) == {'same', 'distinct'}
            and 'faults' in args.phases and 'complexity' in args.phases
            and ('core' in args.phases or set(args.phases) >= {
                'baseline', 'concurrency', 'soak', 'cancellation', 'transport', 'boundaries', 'ascii'}),
        'recorded_http_counts': dict(counts),
        'elapsed_seconds': time.monotonic() - started, 'samples': len(samples),
        'backend_memory_peak_bytes': max((sample['memory_peak_bytes'] for sample in samples), default=0),
        'backend_sampled_memory_max_bytes': max((sample['memory_bytes'] for sample in samples), default=0),
        'temporary_max_bytes': max((sample['temporary_bytes'] for sample in samples), default=0),
        'nginx_temporary_max_bytes': max((sample.get('nginx_temporary_bytes', 0) for sample in samples), default=0),
        'multipart_tmpfs_max_bytes': max((sample.get('multipart_tmpfs_bytes', 0) for sample in samples), default=0),
        'sampled_oom_kill': any(sample['memory_events'].get('oom_kill', 0) for sample in samples),
        'frontend_memory_peak_bytes': max((sample['frontend']['memory_peak_bytes'] for sample in samples
                                           if 'frontend' in sample), default=None),
        'frontend_sampled_oom_kill': any(sample.get('frontend', {}).get('oom_kill', 0) for sample in samples),
        'host_available_min_bytes': min((sample['host_available_bytes'] for sample in samples), default=0)}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2))
    return summary


def interrupted(number, frame):
    raise SystemExit(128 + number)


for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(number, interrupted)

monitor_thread = None
exit_code = 0
try:
    assert available_memory() >= (args.minimum_available_mib + 384) * 2**20, 'Insufficient safe host headroom.'
    assert shutil.disk_usage(output).free > 1024 * 2**20, 'Less than 1 GiB free for private test output.'
    harness = output / 'harness'
    harness.mkdir(mode=0o700)
    source_hashes = {}
    for name in ('load_resilience_client.py', 'load_resilience_probe.py',
                 'load_resilience_fault.py', 'run_load_resilience.py'):
        contents = (ROOT / 'backend/tests' / name).read_bytes()
        (harness / name).write_bytes(contents)
        source_hashes[name] = hashlib.sha256(contents).hexdigest()
    data = json.loads((ROOT / 'config/pricing.demo.json').read_text())
    data['configuration_kind'] = 'private'
    (output / 'synthetic.private.json').write_text(json.dumps(data))
    (output / 'trusted.conf').write_text('set_real_ip_from 127.0.0.254;\n')
    metadata = {'host_cpus': len(os.sched_getaffinity(0)), 'initial_available_bytes': available_memory(),
        'budgets': vars(args), 'backend_image': inspect(BACKEND)['Id'], 'frontend_image': inspect(FRONTEND)['Id'],
        'architecture': os.uname().machine}
    metadata['harness_sha256'] = source_hashes
    docker_root = docker('info', '--format', '{{.DockerRootDir}}', text=True).stdout.strip()
    metadata['docker_free_bytes'] = shutil.disk_usage(docker_root).free
    assert metadata['docker_free_bytes'] > (1024 + args.maximum_storage_mib) * 2**20, 'Insufficient Docker filesystem headroom.'
    # Image inspect and container inspect share the same JSON API shape.
    (output / 'environment.json').write_text(json.dumps(metadata, indent=2))
    BACKEND, FRONTEND = metadata['backend_image'], metadata['frontend_image']
    docker('network', 'create', '--internal', network)
    docker('volume', 'create', volume)
    start_backend()
    docker('run', '-d', '--pull', 'never', '--name', frontend, '--network', network,
           '--cpus', '.5', '--memory', '256m', '--memory-swap', '256m', '--pids-limit', '64',
           '--tmpfs', '/var/cache/nginx:rw,nosuid,nodev,size=96m',
           '-e', 'REQUIRE_TRUSTED_PROXY=true',
           '-v', f'{output / "trusted.conf"}:/etc/nginx/trusted-proxies.conf:ro', FRONTEND)
    docker('exec', frontend, 'nginx', '-t')
    # Fail closed before starting any workload on unsupported/missing telemetry.
    (output / 'initial.json').write_text(json.dumps(probe(), indent=2))
    (output / 'initial_frontend.json').write_text(json.dumps(frontend_metrics(), indent=2))
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    if 'core' in args.phases:
        client('core')
    for phase in ('baseline', 'concurrency', 'soak', 'cancellation', 'transport', 'boundaries', 'ascii'):
        if phase in args.phases:
            client(phase)
            if phase == 'transport':
                collect_logs('transport')
                assert any(line.startswith('127.40.0.4 ') and
                    '"POST /api/analyze HTTP/1.1" 408 ' in line for line in
                    (output / 'transport-frontend.log').read_text().splitlines()), 'Missing Nginx inactivity timeout evidence.'
    if 'faults' in args.phases:
        recreate('storage')
        client('storage')
        recreate('error')
        client('error')
        recreate('hang')
        client('timeout')
        assert probe()['workspaces'] == 0, 'Timeout left owned temporary workspaces.'
        process, log = client('crash', detached=True)
        try:
            deadline = time.monotonic() + 15
            while not (probe()['workspaces'] >= 2 and any(p['slicer'] for p in probe()['processes'])):
                assert time.monotonic() < deadline, 'Fault CLI did not start before crash test.'
                time.sleep(.2)
            docker('kill', '--signal', 'KILL', backend)
            assert process.wait(timeout=15) == 0
        finally:
            log.close()
        docker('start', backend)
        wait_healthy()
        fresh = probe()['workspaces']
        assert fresh >= 2, 'Fresh abandoned workspaces should survive before retention eligibility.'
        aged = json.loads(docker('exec', backend, 'python', '/app/tests/load_resilience_probe.py', '--age').stdout)
        assert aged >= 2
        docker('restart', backend)
        wait_healthy()
        assert probe()['workspaces'] == 0, 'Startup did not recover eligible owned orphans.'
        record_lifecycle('crash_restart_orphan_recovery', fresh_workspaces=fresh, aged_markers=aged, remaining_workspaces=0)
        recreate()
        client('recovered')
    # Most resource-intensive progressive cases run last; safety abort preserves
    # completed evidence and is reported as incomplete, never as a successful run.
    if 'complexity' in args.phases:
        client('complexity')
    deadline = time.monotonic() + 5
    while True:
        final = probe()
        if final['workspaces'] == 0 and not any(item['slicer'] or item['zombie'] for item in final['processes']):
            break
        # Permit transient exit/reap states, not persistent orphan processes.
        assert time.monotonic() < deadline, 'Processing resources did not become idle.'
        time.sleep(.1)
    (output / 'final.json').write_text(json.dumps(final, indent=2))
except BaseException as error:
    failure.append(type(error).__name__ + ': ' + str(error))
    exit_code = 2 if stop.is_set() else 1
finally:
    stop.set()
    if monitor_thread is not None:
        monitor_thread.join(timeout=12)
    collect_logs('final')
    try:
        (output / 'container-state.json').write_text(json.dumps(
            {name: inspect(name)['State'] for name in (backend, frontend)}, indent=2))
    except subprocess.SubprocessError:
        pass
    for command in (['rm', '-f', runner, backend, frontend], ['network', 'rm', network], ['volume', 'rm', volume]):
        try:
            subprocess.run(['docker', *command], capture_output=True, timeout=30)
        except subprocess.SubprocessError:
            failure.append('Owned test-resource cleanup timed out; operator verification required.')
            exit_code = exit_code or 1
    try:
        for command in (['ps', '-a', '--filter', 'name=' + prefix, '--format', '{{.Names}}'],
                        ['network', 'ls', '--filter', 'name=' + prefix, '--format', '{{.Name}}'],
                        ['volume', 'ls', '--filter', 'name=' + prefix, '--format', '{{.Name}}']):
            assert not docker(*command, timeout=8).stdout.strip(), 'Owned test resources remain after cleanup.'
    except (subprocess.SubprocessError, AssertionError):
        failure.append('Owned test-resource cleanup could not be confirmed.')
        exit_code = exit_code or 1
    # Test geometry is generated, bounded and disposable; retain only private
    # diagnostics/metrics and the explicitly synthetic startup fixture.
    (output / 'synthetic-grid.stl').unlink(missing_ok=True)
    result = finish_summary()
    print(json.dumps(result, indent=2), flush=True)
    print('Private results:', output, flush=True)
raise SystemExit(exit_code)
