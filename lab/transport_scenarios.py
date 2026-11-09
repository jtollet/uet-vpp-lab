#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run matched UET reorder and return-queue experiments; retain native evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys

import lab


def clock_us(value):
    hour, minute, second, usec = map(int, value.split(':'))
    return ((hour * 60 + minute) * 60 + second) * 1000000 + usec


def records(text):
    result = []
    for block in re.split(r'\nPacket \d+\n', text)[1:]:
        header = re.search(r'(198\.18\.0\.[12]) -> (198\.18\.0\.[12]) PDS (\w+)[^\n]*', block)
        if not header:
            continue
        row = dict(source=header[1], destination=header[2], kind=header[3], trace=block)
        for name in ('RETX', 'SPDCID', 'DPDCID', 'PSN', 'CACK_PSN'):
            match = re.search(r'\b' + name + r' (\d+)\b', header[0])
            row[name.lower()] = int(match[1]) if match else None
        row['nodes'] = [(node, clock_us(timestamp)) for timestamp, node in re.findall(
            r'^(\d+:\d+:\d+:\d+): ([^\n]+)$', block, re.MULTILINE)]
        result.append(row)
    return result


def counter(text, node, reason):
    return sum(int(count) for count, found_node, found_reason in re.findall(
        r'^\s*(\d+)\s+(\S+)\s+(.+?)\s+(?:error|warning|warn|info)\s*$', text, re.MULTILINE)
        # Native CLI columns may truncate long reasons (e.g. "... lack of
        # spac"). Match that displayed prefix against the full known reason.
        if found_node == node and reason.startswith(found_reason.strip()))


def summarize(report):
    directory = Path(report['results'])
    rows = {side: records((directory / f'{side}-show-trace.txt').read_text()) for side in ('a', 'b')}
    summary = {'settings': report, 'nsim': {}, 'return_wheel_wait_us': {}, 'selected': {}}
    summary['policer_packets'] = dict((name, int(value)) for name, value in re.findall(
        r'^(conform|exceed|violate) (\d+) packets',
        (directory / 'b-show-policer.txt').read_text(), re.MULTILINE))
    summary['application_messages'] = {}
    for side in ('a', 'b'):
        errors = (directory / f'{side}-show-errors.txt').read_text()
        summary['nsim'][side] = {
            'reordered': counter(errors, 'nsim-output-feature', 'Packets reordered'),
            'queue_drops': counter(errors, 'nsim-output-feature', 'Packets dropped due to lack of space'),
            'injected_loss': counter(errors, 'nsim-output-feature', 'Network loss simulation drop packets'),
        }
    for role in ('client', 'server'):
        log = (directory / f'{role}.log').read_text()
        summary['application_messages'][role] = {
            'no_match': log.count('Msg Rsp: No Match'),
            'bad_generation': log.count('Msg Rsp: Bad Generation')}
    previous = {}
    inversions = []
    for row in rows['b']:
        if 'UET RX:' not in row['trace'] or row['kind'] != 'ROD_REQ' or row['retx'] != 0:
            continue
        key = row['source'], row['destination'], row['spdcid']
        old = previous.get(key)
        if old and 0 < ((old['psn'] - row['psn']) & 0xffffffff) < 0x80000000:
            inversions.append([old, row])
        previous[key] = row
    summary['original_request_arrival_inversions'] = len(inversions)
    if inversions:
        summary['selected']['reordered_arrivals'] = inversions[0]
    for kind in ('ACK', 'ROD_REQ'):
        delays = []
        selected = []
        for row in rows['b']:
            nodes = dict(row['nodes'])
            if row['kind'] == kind and 'uet-input' in nodes and 'nsim-wheel' in nodes:
                delay = nodes['nsim-wheel'] - nodes['uet-input']
                if delay >= 0:
                    delays.append(delay)
                    selected.append((delay, row))
        if delays:
            ordered = sorted(delays)
            summary['return_wheel_wait_us'][kind] = {
                'count': len(delays), 'median': statistics.median(delays),
                'p95': ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 'max': max(delays)}
            summary['selected'][f'return_{kind.lower()}'] = max(selected, key=lambda item: item[0])[1]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--scenario', choices=('all', 'reorder', 'qos'), default='all')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error('--repeats must be between 1 and 10')
    lab.require_root()
    root = args.root.resolve()
    output = args.output or root / ('transport-evidence-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    output.mkdir(parents=True, exist_ok=False)
    common = ['--iterations', '30', '--trace-packets', '4000', '--sample-ms', '200']
    cases = []
    if args.scenario in ('all', 'reorder'):
        cases += [('reorder-control', []), ('reorder', ['--reorder-fraction', '0.25', '--reorder-delay-ms', '1'])]
    if args.scenario in ('all', 'qos'):
        queue = ['--size', '64', '--reverse-delay-ms', '0.1', '--reverse-buffer-ms', '20',
                 '--reverse-bandwidth-mbps', '40']
        for repeat in range(args.repeats):
            cases += [(f'return-quiet-{repeat + 1}', queue),
                      (f'return-loaded-{repeat + 1}', queue + ['--background-pps', '5000']),
                      (f'return-policed-{repeat + 1}', queue + ['--background-pps', '5000',
                                                              '--background-policer-mbps', '6'])]
    evidence = {'machine': platform.machine(), 'kernel': platform.release(), 'runs': []}
    for name, options in cases:
        command = [sys.executable, str(Path(__file__).with_name('lab.py')), '--root', str(root),
                   'run'] + common + options
        process = subprocess.run(command, capture_output=True, text=True)
        (output / f'{name}-launcher.log').write_text(process.stdout + process.stderr)
        match = re.search(r'"results": "([^"]+)"', process.stdout)
        if not match:
            raise RuntimeError(f'{name}: launcher failed; see {output / (name + "-launcher.log")}')
        report = json.loads((Path(match[1]) / 'report.json').read_text())
        row = summarize(report)
        row['name'] = name
        evidence['runs'].append(row)
        (output / 'summary.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(name, report['outcome'], report['pds_retransmit_warning_lines'],
              row['original_request_arrival_inversions'], row['return_wheel_wait_us'], flush=True)
        expected_overload = (name.startswith('return-loaded-')
                             and report['outcome'] == 'application_error')
        if process.returncode and not expected_overload:
            raise RuntimeError(f'{name}: application did not complete; evidence saved in {output}')
        if name == 'reorder':
            if (not row['original_request_arrival_inversions'] or not row['nsim']['a']['reordered']
                    or any(value for counters in row['nsim'].values()
                           for key, value in counters.items() if key != 'reordered')):
                raise RuntimeError('Reorder case needs observed arrival inversions and zero nsim drops')
        if name.startswith('return-policed-'):
            packets = row['policer_packets']
            if not packets.get('conform') or not (packets.get('exceed', 0) + packets.get('violate', 0)):
                raise RuntimeError('QoS control must both admit and discard background packets')
        if expected_overload:
            # Persistent overload may exhaust the retry budget. Record that
            # outcome, then recreate this isolated lab to release all buffer
            # ownership before the matched QoS repair experiment.
            restart = []
            for action in ('down', 'up'):
                restart.append(subprocess.check_output(
                    [sys.executable, str(Path(__file__).with_name('lab.py')),
                     '--root', str(root), action], text=True))
            (output / f'{name}-restart.log').write_text('\n'.join(restart))
    print('Evidence:', output)


if __name__ == '__main__':
    main()
