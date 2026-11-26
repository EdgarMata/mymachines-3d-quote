"""Bounded HTTP workload inside an isolated frontend network namespace."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import socket
import struct
import threading
import time

import trimesh


parser = argparse.ArgumentParser()
parser.add_argument('phase', choices=['core', 'baseline', 'concurrency', 'soak', 'cancellation',
    'transport', 'boundaries', 'ascii', 'storage', 'error', 'timeout', 'crash', 'recovered', 'complexity'])
parser.add_argument('--output', required=True)
parser.add_argument('--soak-seconds', type=int, default=180)
parser.add_argument('--skip-near-volume-estimate', action='store_true')
parser.add_argument('--concurrency-levels', type=int, nargs='+', choices=(1, 2, 5, 10, 20), default=(1, 2, 5, 10, 20))
parser.add_argument('--origin-modes', nargs='+', choices=('same', 'distinct'), default=('same', 'distinct'))
parser.add_argument('--concurrency-repeats', type=int, choices=range(1, 6), default=1)
args = parser.parse_args()
os.umask(0o077)
output = Path(args.output)
records = []
small = trimesh.creation.box(extents=[10, 20, 30]).export(file_type='stl')
complex_model = trimesh.creation.icosphere(subdivisions=5, radius=30).export(file_type='stl')


def save(record):
    records.append(record)
    with output.open('a') as stream:
        stream.write(json.dumps(record) + '\n')
    print(record['case'], record.get('status', record.get('counts', 'done')), flush=True)


def body(contents):
    return (b'--mm008\r\nContent-Disposition: form-data; name="file"; filename="synthetic.stl"\r\n'
            b'Content-Type: model/stl\r\n\r\n' + contents + b'\r\n--mm008--\r\n')


def request(case, path, contents=None, *, source='127.10.0.1', direct=False, headers=None,
            expected=(200,), record=True, timeout=95):
    connection = http.client.HTTPConnection('backend' if direct else '127.0.0.1',
        8000 if direct else 80, timeout=timeout, source_address=None if direct else (source, 0))
    payload = None if contents is None else body(contents)
    supplied = dict(headers or {})
    if payload is not None:
        supplied['Content-Type'] = 'multipart/form-data; boundary=mm008'
    start = time.time()
    try:
        connection.request('GET' if payload is None else 'POST',
                           path if direct else '/api' + path, payload, supplied)
        response = connection.getresponse()
        raw = response.read(128 * 1024)
        try:
            value = json.loads(raw)
        except ValueError:
            value = {}
        result = {'case': case, 'start': start, 'end': time.time(), 'status': response.status,
                  'duration_seconds': time.time() - start, 'file_bytes': len(contents or b''),
                  'response_bytes': len(raw), 'direct': direct}
        if isinstance(value, dict):
            result['application_status'] = value.get('status')
            result['estimated_print_seconds'] = (value.get('slicing') or {}).get('print_time_seconds')
            result['geometry_faces'] = (value.get('geometry') or {}).get('faces')
        if record or response.status not in expected:
            save(result)
        assert response.status in expected, (case, response.status)
        assert not any(secret in raw for secret in (b'cost_per_kg_eur', b'electricity_cost_per_kwh_eur',
            b'/run/mymachines/', b'/project/', b'Traceback', b'SYNTHETIC_PRIVATE_CONFIG'))
        return result, value
    finally:
        connection.close()


def idle():
    # A successful heavy request, not just health, establishes recovered admission.
    deadline = time.monotonic() + 35
    while True:
        result, _ = request('admission_probe', '/analyze', small, direct=True,
                            expected=(200, 503), record=False)
        if result['status'] == 200:
            return
        assert time.monotonic() < deadline, 'Admission did not recover.'
        time.sleep(.2)


def concurrency():
    for origins in args.origin_modes:
        levels = list(args.concurrency_levels) * args.concurrency_repeats
        for group, clients in enumerate(levels, 1):
            time.sleep(2.2)  # Drain the unchanged global bucket, not individual pools.
            barrier = threading.Barrier(clients)
            source_group = group + (20 if origins == 'distinct' else 0)

            def run(index):
                source = f'127.{21 if origins == "distinct" else 20}.{source_group}.{index + 1 if origins == "distinct" else 1}'
                barrier.wait(timeout=10)
                return request('concurrency_member', f'/estimate?mm008_client={source_group}_{index}',
                               complex_model, source=source, expected=(200, 429, 500, 502, 503, 504),
                               record=False)[0]

            start = time.time()
            with ThreadPoolExecutor(max_workers=clients) as pool:
                futures = [pool.submit(run, index) for index in range(clients)]
                time.sleep(.1)
                pending = sum(not future.done() for future in futures)
                request('concurrency_health', '/health', source='127.20.255.1')
                results = [future.result(timeout=95) for future in futures]
            counts = dict(Counter(str(item['status']) for item in results))
            idle()
            save({'case': 'concurrency', 'origins': origins, 'clients': clients,
                  'start': start, 'end': time.time(), 'counts': counts,
                  'pending_at_health_check': pending,
                  'maximum_response_seconds': max(item['duration_seconds'] for item in results)})
            assert pending >= 1, 'Could not observe health during an in-flight request.'
            assert counts.get('200', 0) >= 1, (origins, clients, counts)
            assert set(counts) <= {'200', '429', '503'}, (origins, clients, counts)
    global_rate_burst()


def global_rate_burst():
    # Two requests per distinct IP stay below each individual burst. Keep at
    # most 20 clients active; any 429 here demonstrates the shared rate budget.
    time.sleep(2.2)
    barrier = threading.Barrier(20)

    def run(index):
        barrier.wait(timeout=10)
        return [request('global_rate_member', '/estimate', small, source=f'127.80.0.{index + 1}',
                        expected=(200, 429, 500, 502, 503, 504), record=False)[0] for _ in range(2)]

    start = time.time()
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(run, index) for index in range(20)]
        time.sleep(.1)
        request('global_burst_health', '/health')
        results = [result for future in futures for result in future.result(timeout=95)]
    counts = dict(Counter(str(result['status']) for result in results))
    save({'case': 'global_rate_burst', 'clients': 20, 'requests_per_ip': 2,
          'counts': counts, 'start': start, 'end': time.time()})
    assert counts.get('429', 0) > 0
    assert set(counts) <= {'200', '429', '503'}, counts
    idle()


def soak():
    deadline = time.monotonic() + args.soak_seconds
    start = time.time()
    cycle = 0
    counts = Counter()
    while time.monotonic() < deadline:
        source = f'127.30.0.{cycle % 20 + 1}'
        for path, model, expected in (('/analyze', complex_model, (200,)),
                                     ('/estimate', small, (200,)),
                                     ('/analyze', b'not an STL', (400,))):
            result, _ = request('soak_member', path, model, source=source, expected=expected)
            counts[str(result['status'])] += 1
        cycle += 1
        time.sleep(1)
    idle()
    save({'case': 'soak', 'start': start, 'end': time.time(), 'cycles': cycle, 'counts': dict(counts)})


def upload_socket(source, *, direct=False, length=2 * 1024 * 1024):
    connection = socket.create_connection(('backend' if direct else '127.0.0.1', 8000 if direct else 80),
        timeout=5, source_address=None if direct else (source, 0))
    connection.sendall((f'POST {"" if direct else "/api"}/analyze HTTP/1.1\r\nHost: local\r\n'
        f'Content-Length: {length}\r\nContent-Type: multipart/form-data; boundary=mm008\r\n'
        'Connection: close\r\n\r\n').encode() + body(small)[:128])
    return connection


def transport():
    sockets = [upload_socket('127.40.0.1') for _ in range(5)]
    try:
        time.sleep(.3)
        request('active_upload_slot_limit', '/options', source='127.40.0.1', expected=(429,))
        request('health_during_slow_uploads', '/health', source='127.40.0.1')
    finally:
        for connection in sockets:
            connection.close()
    time.sleep(.5)
    connection = upload_socket('127.40.0.2', direct=True)
    try:
        time.sleep(.2)
        request('backend_upload_admission', '/analyze', small, direct=True, expected=(503,))
    finally:
        connection.close()
    idle()
    save({'case': 'interrupted_upload_recovered', 'status': 'passed'})

    # Keep progressing beyond the 30-second inactivity timeout, then disconnect.
    connection = upload_socket('127.40.0.3')
    start = time.time()
    try:
        for _ in range(7):
            connection.sendall(b'X' * 1024)
            time.sleep(5)
        connection.settimeout(.2)
        try:
            data = connection.recv(1024)
        except socket.timeout:
            pass
        else:
            raise AssertionError('Progressing upload was answered or closed before interruption.')
    finally:
        connection.close()
    save({'case': 'continuously_slow_upload', 'duration_seconds': time.time() - start,
          'observation': 'remained open beyond inactivity timeout; deliberately interrupted'})
    connection = upload_socket('127.40.0.4')
    start = time.time()
    try:
        connection.settimeout(35)
        response = connection.recv(4096)
        assert not response or b' 408 ' in response.split(b'\r\n', 1)[0], response[:100]
        assert 28 <= time.time() - start <= 35, 'Unexpected inactivity timeout duration.'
    finally:
        connection.close()
    save({'case': 'inactive_upload_timeout', 'status': 408 if response else 'connection_closed',
          'duration_seconds': time.time() - start})
    idle()


def cancellation():
    connection = http.client.HTTPConnection('backend', 8000, timeout=5)
    connection.request('POST', '/estimate', body(complex_model),
                       {'Content-Type': 'multipart/form-data; boundary=mm008'})
    result, _ = request('cancel_before_disconnect_busy', '/analyze', small, direct=True,
                         expected=(503,), record=True)
    connection.close()
    request('cancel_health', '/health', direct=True)
    idle()
    request('cancel_followup_quote', '/estimate', small, direct=True)


def boundaries():
    for quantity, expected in ((0, 400), (1, 200), (100, 200), (101, 400)):
        request('quantity_' + str(quantity), '/estimate?quantity=' + str(quantity), small,
                source=f'127.50.0.{quantity % 250 + 1}', expected=(expected,))
    for index, (model, label) in enumerate(((b'broken STL', 'corrupt'),
            (trimesh.Trimesh(vertices=[[0, 0, 0]] * 3, faces=[[0, 1, 2]]).export(file_type='stl'), 'degenerate'),
            (trimesh.creation.box(extents=[1e30] * 3).export(file_type='stl'), 'extreme_finite'))):
        request(label, '/analyze', model, source=f'127.50.1.{index + 1}',
                expected=(200,) if label == 'extreme_finite' else (400,))
    connection = http.client.HTTPConnection('backend', 8000, timeout=5)
    try:
        connection.request('POST', '/analyze', b'broken multipart',
                           {'Content-Type': 'multipart/form-data; boundary=mm008'})
        response = connection.getresponse()
        response.read()
        assert response.status in (400, 422)
        save({'case': 'invalid_multipart', 'status': response.status})
    finally:
        connection.close()
    # Header-only oversize, no large network transfer needed.
    for direct in (False, True):
        connection = http.client.HTTPConnection('backend' if direct else '127.0.0.1', 8000 if direct else 80, timeout=5)
        try:
            connection.putrequest('POST', '/analyze' if direct else '/api/analyze')
            connection.putheader('Content-Length', str(52 * 1024 * 1024))
            connection.putheader('Content-Type', 'multipart/form-data; boundary=mm008')
            connection.endheaders()
            response = connection.getresponse()
            response.read()
            assert response.status == 413
            save({'case': 'oversize_declared_body', 'direct': direct, 'status': response.status})
        finally:
            connection.close()
    # Just below 50 MiB, with too many facets: must reject before full geometry.
    facet = small[84:134]
    count = (50 * 1024 * 1024 - 84) // 50
    oversized_complexity = b'MM008 synthetic'.ljust(80, b' ') + struct.pack('<I', count) + facet * count
    request('near_50_MiB', '/analyze', oversized_complexity, source='127.50.2.1', expected=(413,))
    del oversized_complexity
    idle()


def complexity():
    # A deterministic, nondegenerate open tessellated grid. Binary facets are
    # streamed to one bounded fixture; this is not duplicated-box geometry.
    for index, count in enumerate((10_000, 100_000, 250_000, 500_000, 1_000_000)):
        path = output.parent / 'synthetic-grid.stl'
        with path.open('wb') as stream:
            stream.write(b'MM008 synthetic grid'.ljust(80, b' ') + struct.pack('<I', count))
            for number in range(count):
                cell, half = divmod(number, 2)
                x, y = cell % 1000, cell // 1000
                vertices = (x, y, 0, x + 1, y, 0, x, y + 1, 0) if not half else (
                    x + 1, y, 0, x + 1, y + 1, 0, x, y + 1, 0)
                stream.write(struct.pack('<12fH', 0, 0, 1, *vertices, 0))
        try:
            _, result = request(f'complexity_{count}', '/analyze', path.read_bytes(),
                                source=f'127.60.0.{index + 1}', timeout=45)
            # /analyze intentionally exposes orientation, not raw geometry counts.
            assert result['orientation']['dimensions_mm'] == {'x': 1000.0, 'y': count / 2000, 'z': 0.0}
            idle()
        finally:
            path.unlink(missing_ok=True)


def ascii_boundaries():
    # Legitimate ASCII whitespace separates byte-budget behavior from geometry
    # complexity. No giant source fixture is added to the repository.
    ascii_model = trimesh.creation.box(extents=[10, 20, 30]).export(file_type='stl_ascii').encode()
    head, tail = ascii_model.rsplit(b'endsolid', 1)
    line = b' ' * 4095 + b'\n'  # Fits the existing 4096-byte parser line limit.
    for index, size in enumerate((10 * 2**20, 25 * 2**20, 50 * 2**20 - 1024)):
        padding = line * ((size - len(ascii_model)) // len(line))
        contents = b''.join((head, padding, b'endsolid', tail))
        del padding
        _, response = request(f'ascii_near_{size}', '/analyze', contents, source=f'127.90.0.{index + 1}')
        assert response['status'] == 'ready'
        assert response['orientation']['dimensions_mm'] == {'x': 10.0, 'y': 20.0, 'z': 30.0}
        del contents
        idle()


def baseline():
    for index, (label, model) in enumerate((('small', small),
            ('medium', trimesh.creation.box(extents=[80, 60, 40]).export(file_type='stl')),
            ('complex', complex_model),
            ('near_build_volume', trimesh.creation.box(extents=[245, 205, 205]).export(file_type='stl')),
            ('incompatible', trimesh.creation.box(extents=[1100] * 3).export(file_type='stl')))):
        for path in ('/analyze', '/estimate'):
            if args.skip_near_volume_estimate and label == 'near_build_volume' and path == '/estimate':
                save({'case': 'baseline_near_build_volume/estimate', 'skipped': True,
                      'reason': 'Explicit host safety exclusion after a previous monitored interruption.'})
                continue
            _, result = request('baseline_' + label + path, path, model, source=f'127.10.{index}.1')
            expected = ('model_exceeds_build_volume' if label == 'incompatible' else
                        'ready' if path == '/analyze' else 'estimated')
            assert result['status'] == expected
if args.phase == 'core':
    baseline()
    concurrency()
    soak()
    cancellation()
    transport()
    boundaries()
    ascii_boundaries()
elif args.phase in ('baseline', 'concurrency', 'soak', 'cancellation', 'transport', 'boundaries'):
    globals()[args.phase]()
elif args.phase == 'complexity':
    complexity()
elif args.phase == 'ascii':
    ascii_boundaries()
elif args.phase == 'error':
    request('injected_process_error', '/estimate', small, direct=True, expected=(500,))
    idle()
elif args.phase == 'storage':
    request('injected_temporary_disk_full', '/analyze', complex_model, direct=True, expected=(500,))
    idle()
    request('disk_full_followup_small_analysis', '/analyze', small, direct=True)
elif args.phase == 'timeout':
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(request, 'injected_cli_timeout', '/estimate', small,
                             source='127.70.0.1', expected=(504,))
        for _ in range(3):
            time.sleep(2)
            request('timeout_health', '/health', direct=True)
            request('timeout_admission', '/analyze', small, direct=True, expected=(503,))
        result, _ = future.result(timeout=75)
        assert 59 <= result['duration_seconds'] <= 75
    idle()
elif args.phase == 'crash':
    try:
        request('backend_killed_in_flight', '/estimate', small, direct=True, expected=(502,))
    except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
        save({'case': 'backend_killed_in_flight', 'observation': 'connection interrupted as expected'})
elif args.phase == 'recovered':
    idle()
    request('recovered_real_orca_quote', '/estimate', small, direct=True)
