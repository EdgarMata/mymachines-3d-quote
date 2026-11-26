"""Summarize private opt-in observations without publishing infrastructure data."""

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory', type=Path)
args = parser.parse_args()
directory = args.directory.resolve()
if directory.is_relative_to(Path(__file__).resolve().parents[2]):
    parser.error('Operational observations must remain outside the checkout.')
samples = [json.loads(line) for line in (directory / 'metrics.jsonl').read_text().splitlines()]
if (directory / 'initial.json').exists():
    samples.insert(0, json.loads((directory / 'initial.json').read_text()))
records = [json.loads(line) for line in (directory / 'requests.jsonl').read_text().splitlines()]


def servers(sample):
    return [process for process in sample['processes'] if process['server']]


def summarize_request(record):
    during = [sample for sample in samples if record['start'] <= sample['time'] <= record['end']]
    before = [sample for sample in samples if sample['time'] <= record['start']]
    after = [sample for sample in samples if sample['time'] >= record['end']]
    left, right = (before[-1] if before else None), (after[0] if after else None)
    # A container recreation resets counters. Never subtract across that reset.
    cpu = (right['cpu_usec'] - left['cpu_usec']) / 1e6 if left and right else None
    return {
        'case': record['case'], 'status': record.get('status'),
        'file_bytes': record.get('file_bytes'), 'elapsed_seconds': record['end'] - record['start'],
        'estimated_physical_print_seconds': record.get('estimated_print_seconds'),
        'cpu_seconds_bracketed': cpu if cpu is not None and cpu >= 0 and len(during) >= 2 else None,
        'memory_before_bytes': left['memory_bytes'] if left else None,
        'memory_after_bytes': right['memory_bytes'] if right else None,
        'memory_sampled_peak_bytes': max(sample['memory_bytes'] for sample in during) if during else None,
        'temporary_sampled_peak_bytes': max(sample['temporary_bytes'] for sample in during) if during else None,
        'samples_during_request': len(during),
    }


result = {'baseline_and_complexity': [summarize_request(record) for record in records
    if (record['case'].startswith(('baseline_', 'complexity_', 'ascii_near_')) and 'start' in record)],
    'concurrency': [record for record in records if record['case'] == 'concurrency']}
soaks = [record for record in records if record['case'] == 'soak']
if soaks:
    soak = soaks[-1]
    observations = [sample for sample in samples if soak['start'] <= sample['time'] <= soak['end']]
    edge = max(1, len(observations) // 5)

    def window_metrics(window):
        processes = [process for sample in window for process in servers(sample)]
        return {'server_rss_median_kib': median(process['rss_kib'] for process in processes),
                'server_fds_median': median(process['fds'] for process in processes),
                'server_threads_median': median(process['threads'] for process in processes),
                'temporary_sampled_max_bytes': max(sample['temporary_bytes'] for sample in window)}

    def percentile(values, fraction):
        return sorted(values)[min(len(values) - 1, round((len(values) - 1) * fraction))]

    members = [record for record in records if record['case'] == 'soak_member']
    latencies = [record['duration_seconds'] for record in members]
    result['soak'] = {'cycles': soak['cycles'], 'duration_seconds': soak['end'] - soak['start'],
        'counts': dict(Counter(str(record['status']) for record in members)),
        'early': window_metrics(observations[:edge]), 'late': window_metrics(observations[-edge:]),
        'response_median_seconds': median(latencies), 'response_p95_seconds': percentile(latencies, .95)}
result['sampled_maximum_slicer_groups'] = max((len({process.get('pgid', process['pid'])
    for process in sample['processes'] if process['slicer']}) for sample in samples), default=0)
result['sampled_zombies'] = sum(process['zombie'] for sample in samples for process in sample['processes'])
frontend = [sample['frontend'] for sample in samples if 'frontend' in sample]
if frontend:
    initial = (json.loads((directory / 'initial_frontend.json').read_text())
               if (directory / 'initial_frontend.json').exists() else frontend[0])
    result['frontend'] = {'memory_before_bytes': initial['memory_bytes'],
        'memory_after_bytes': frontend[-1]['memory_bytes'],
        'memory_peak_bytes': max(item['memory_peak_bytes'] for item in frontend),
        'cpu_seconds': (frontend[-1]['cpu_usec'] - initial['cpu_usec']) / 1e6,
        'oom_kill': any(item['oom_kill'] for item in frontend)}
target = directory / 'analysis.json'
target.write_text(json.dumps(result, indent=2))
target.chmod(0o600)
print('Private summary written:', target)
