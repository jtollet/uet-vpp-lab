#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Two VPPs, memif and a client based on official uet.c; no physical interfaces."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time


def require_root():
    if os.geteuid() != 0:
        raise SystemExit('Run lab.py with sudo; build.sh runs as your regular user.')


def paths(root):
    prefix = root / 'vpp-install'
    lib = next(prefix.rglob('libvppinfra.so')).parent
    env = {k: v for k, v in os.environ.items() if not k.startswith('UET_')}
    env['LD_LIBRARY_PATH'] = ':'.join(map(str, [root / 'uet-ref-prov',
        root / 'uet-trace-plugin-build/lib', lib, root / 'libfabric/src/.libs']))
    return prefix, lib, env


def read_state(root):
    return json.loads((root / 'lab-state.json').read_text())


def cli(root, state, side, command, check=True):
    prefix, _, env = paths(root)
    result = subprocess.run([str(prefix / 'bin/vppctl'), '-s',
        str(Path(state['runtime']) / side / 'cli.sock'), command],
        env=env, capture_output=True, text=True, timeout=15)
    text = (result.stdout + result.stderr).replace('\r', '')
    if check and (result.returncode or any(word in text.lower() for word in
            ('unknown input', 'unknown interface', 'failed:', 'unknown function'))):
        raise RuntimeError(f'{side}: {command}\n{text}')
    return text


def up(root):
    require_root()
    if (root / 'lab-state.json').exists():
        raise SystemExit('A lab state already exists. Run down first.')
    prefix, lib, env = paths(root)
    cores = sorted(os.sched_getaffinity(0))
    if len(cores) < 4:
        raise SystemExit('Use at least four available logical CPUs.')
    runtime = Path(tempfile.mkdtemp(prefix='uet-obs-', dir='/tmp'))
    os.chmod(runtime, 0o755)
    state = {'runtime': str(runtime), 'pids': {}, 'segments': {}, 'ready': False}
    save = lambda: (root / 'lab-state.json').write_text(json.dumps(state, indent=2))
    plugin_path = str(root / 'uet-trace-plugin-build/lib/vpp_plugins') + ':' + str(lib / 'vpp_plugins')
    try:
        for idx, side in enumerate(('a', 'b')):
            directory = runtime / side
            directory.mkdir()
            namespace = runtime.name + '-' + side
            state['segments'][side] = namespace
            config = directory / 'startup.conf'
            config.write_text(f'''unix {{
  nodaemon
  runtime-dir {directory}
  cli-listen {directory}/cli.sock
  log {directory}/vpp.log
}}
api-segment {{ prefix {namespace} }}
statseg {{ socket-name {directory}/stats.sock }}
cpu {{ main-core {cores[2*idx]} corelist-workers {cores[2*idx+1]} }}
buffers {{ page-size default buffers-per-numa 16384 }}
plugins {{
  path {plugin_path}
  plugin default {{ disable }}
  plugin memif_plugin.so {{ enable }}
  plugin nsim_plugin.so {{ enable }}
  plugin tracepath_plugin.so {{ enable }}
  plugin policer_plugin.so {{ enable }}
  plugin uet_plugin.so {{ enable }}
}}
''')
            with (directory / 'stdout.log').open('w') as out:
                process = subprocess.Popen([str(prefix / 'bin/vpp'), '-c', str(config)],
                    stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            state['pids'][side] = process.pid
            save()
            for _ in range(200):
                if (directory / 'cli.sock').is_socket():
                    break
                if process.poll() is not None:
                    raise RuntimeError((directory / 'stdout.log').read_text())
                time.sleep(0.05)
            else:
                raise RuntimeError(f'VPP {side} did not create its CLI socket')
            role = 'master' if side == 'a' else 'slave'
            mac = f'02:00:00:00:00:0{idx+1}'
            for command in (
                f'create memif socket id 1 filename {runtime}/memif.sock',
                f'create interface memif socket-id 1 id 0 {role} hw-addr {mac}',
                'set interface state memif1/0 up',
                f'set interface ip address memif1/0 198.18.0.{idx+1}/30',
                f'set ip neighbor memif1/0 198.18.0.{2-idx} 02:00:00:00:00:0{2-idx} static',
                'uet enable', f'uet svm create name {namespace} queue-size 256'):
                cli(root, state, side, command)
        time.sleep(1)
        state['ready'] = True
        save()
        print(json.dumps(state, indent=2))
        print(cli(root, state, 'a', 'show version').strip())
        print(cli(root, state, 'a', 'show memif').strip())
    except BaseException:
        save()
        print(f'Lab startup failed; logs: {runtime}')
        down(root)
        raise


def down(root):
    require_root()
    if not (root / 'lab-state.json').exists():
        return
    state = read_state(root)
    for side, pid in state['pids'].items():
        config = str(Path(state['runtime']) / side / 'startup.conf')
        try:
            cmdline = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            if config.encode() not in cmdline:
                raise RuntimeError(f'PID {pid} does not match this lab; refusing to stop it')
            cli(root, state, side, 'uet svm delete name ' + state['segments'][side], check=False)
            os.kill(pid, signal.SIGTERM)
            for _ in range(100):
                if not Path(f'/proc/{pid}/exe').exists():
                    break
                time.sleep(0.05)
            else:
                if config.encode() in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0'):
                    os.kill(pid, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass
    (root / 'lab-state.json').unlink()
    print('Stopped this lab. Logs retained in ' + state['runtime'])


def set_nsim_feature(root, state, side, enabled):
    def count():
        text = cli(root, state, side, 'show interface features memif1/0')
        return sum(line.strip() == 'nsim-output-feature' for line in text.splitlines())

    active = count()
    if active > 1:
        raise RuntimeError('Repeated nsim feature registration; stop and recreate the lab.')
    if bool(active) != enabled:
        command = 'nsim output-feature enable-disable memif1/0'
        cli(root, state, side, command if enabled else command + ' disable')
    if count() != int(enabled):
        raise RuntimeError(f'VPP {side}: nsim output feature does not match requested state')


def nsim(root, state, fraction, pulse_ms=0, delay_ms=0.1, side='a',
         bandwidth_mbps=1000, buffer_ms=0, reorder_fraction=0, reorder_delay_ms=1):
    command = (f'set nsim delay {delay_ms * 1000:g} us '
               f'bandwidth {bandwidth_mbps:g} mbps packet-size 1500 ')
    if buffer_ms:
        command += f'buffer {buffer_ms:g} ms '
    if reorder_fraction:
        command += f'reorder-fraction {reorder_fraction:g} reorder-delay {reorder_delay_ms:g} ms '
    if pulse_ms:
        command += f'drop-once after 1 ms for {pulse_ms} ms'
    else:
        command += f'drop-fraction {fraction}'
    cli(root, state, side, command)
    set_nsim_feature(root, state, side, True)
    return command


def background(root, state, directory, pps, police_mbps=0):
    """Generate ordinary UDP on B's worker, sharing the UET return queue."""
    commands = []
    if not re.search(r'^pg0\s', cli(root, state, 'b', 'show interface'), re.MULTILINE):
        commands += ['create packet-generator interface pg0 hw-addr 02:00:00:00:10:01',
                     'set interface state pg0 up',
                     'set interface ip address pg0 198.19.0.1/30']
    if police_mbps:
        commands += [f'policer add name background type 1r2c rate kbps cir {police_mbps * 1000:g} '
                     'cb 6000 conform-action transmit exceed-action drop violate-action drop',
                     'policer input name background pg0']
    # Write one ordinary Ethernet/IPv4/UDP frame, using only Python's standard
    # library. Replaying it through PG keeps packet construction out of VPP's
    # UET plugin and requires no external traffic generator.
    import socket
    import struct
    payload = b'VPP background traffic'.ljust(1472, b'.')
    udp = struct.pack('!HHHH', 40000, 40001, 8 + len(payload), 0) + payload
    ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(udp), 1, 0, 64, 17, 0,
                     socket.inet_aton('198.19.0.2'), socket.inet_aton('198.18.0.1'))
    words = struct.unpack('!10H', ip)
    checksum = sum(words)
    while checksum >> 16:
        checksum = (checksum & 65535) + (checksum >> 16)
    ip = ip[:10] + struct.pack('!H', (~checksum) & 65535) + ip[12:]
    packet = bytes.fromhex('0200000010010200000010020800') + ip + udp
    pcap = directory / 'background.pcap'
    pcap.write_bytes(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
                     + struct.pack('<IIII', 0, 0, len(packet), len(packet)) + packet)
    commands += [f'packet-generator new name background source pg0 pcap {pcap} '
                 f'rate {pps:g} limit 100000000 worker 0',
                 'packet-generator enable-stream background']
    for command in commands:
        cli(root, state, 'b', command)
    return commands


def stop_background(root, state, police_mbps=0):
    cli(root, state, 'b', 'packet-generator disable-stream background', check=False)
    cli(root, state, 'b', 'packet-generator delete background', check=False)
    if police_mbps:
        cli(root, state, 'b', 'policer input unapply name background pg0', check=False)
        cli(root, state, 'b', 'policer del name background', check=False)


def sample_uet(root, state, directory, elapsed, samples):
    sample_dir = directory / 'live' / f'{len(samples):04d}-{elapsed:.3f}s'
    sample_dir.mkdir(parents=True)
    for side in ('a', 'b'):
        (sample_dir / f'{side}-show-uet.txt').write_text(cli(root, state, side, 'show uet'))
    samples.append({'elapsed_seconds': elapsed, 'directory': str(sample_dir.relative_to(directory))})


def snapshot(root, state, directory, max_traces):
    directory.mkdir(parents=True, exist_ok=True)
    for side in ('a', 'b'):
        commands = ['show version', 'show threads', 'show plugins', 'show memif',
                        'show hardware-interfaces', 'show interface', 'show interface address',
                        'show ip fib', 'show ip neighbors', 'show interface features memif1/0',
                        'show nsim verbose', 'show nsim', 'show errors', 'show uet', 'show trace',
                    'show packet-generator', 'show packet-generator verbose', 'show policer',
                    'show trace paths', 'show runtime', 'show buffers']
        if re.search(r'^pg0\s', cli(root, state, side, 'show interface'), re.MULTILINE):
            commands += ['show interface pg0', 'show interface features pg0']
        for command in commands:
            filename = side + '-' + command.replace(' ', '-').replace('/', '-') + '.txt'
            (directory / filename).write_text(
                cli(root, state, side,
                    f'show trace max {max_traces}' if command == 'show trace' else command))


def run_case(root, args):
    require_root()
    state = read_state(root)
    if not state['ready']:
        raise RuntimeError('Lab is not ready')
    case = (f'{time.time_ns()}-loss-{args.loss:g}-pulse-{args.pulse_ms:g}'
            f'-mtu-{args.mtu}-delay-{args.delay_ms:g}-{args.reverse_delay_ms:g}'
            f'-rto-{args.tx_timeout_ms}')
    directory = Path(state['runtime']) / 'results' / case
    directory.mkdir(parents=True)
    setup = nsim(root, state, args.loss, args.pulse_ms, args.delay_ms,
                 reorder_fraction=args.reorder_fraction, reorder_delay_ms=args.reorder_delay_ms)
    reverse_setup = None
    if args.reverse_delay_ms or args.reverse_buffer_ms:
        reverse_setup = nsim(root, state, 0, delay_ms=args.reverse_delay_ms or 0.1, side='b',
                             bandwidth_mbps=args.reverse_bandwidth_mbps,
                             buffer_ms=args.reverse_buffer_ms)
    else:
        set_nsim_feature(root, state, 'b', False)
    for side in ('a', 'b'):
        cli(root, state, side, f'set interface mtu ip4 {args.mtu} memif1/0')
    if not args.manual_trace:
        for side in ('a', 'b'):
            for command in ('clear trace', 'clear errors', 'clear interfaces', 'clear uet counters',
                            f'trace add uet-input {args.trace_packets}',
                            f'trace add memif-input {args.trace_packets}'):
                cli(root, state, side, command)
    prefix, lib, base_env = paths(root)
    binary = str(root / 'uet_demo')
    processes = []
    samples = []
    terminated = []
    background_commands = []
    started = time.monotonic()
    try:
        if args.background_pps:
            background_commands = background(root, state, directory, args.background_pps,
                                             args.background_policer_mbps)
            cli(root, state, 'b', f'trace add pg-input {min(20, args.trace_packets)}')
            time.sleep(0.1)
        for side in ('b', 'a'):
            env = dict(base_env)
            peer_side = 'b' if side == 'a' else 'a'
            peer_state = cli(root, state, peer_side, 'show uet')
            peer_namespace = int(re.search(r'^application-\d+-namespace (\d+)$',
                peer_state, re.MULTILINE).group(1))
            env.update(UET_NIC_SHIM='vpp', UET_PDS='pds', UET_PKT_DROP_THRESH='0',
                UET_PDS_TX_TIMEOUT=str(args.tx_timeout_ms),
                UET_PDS_MAX_TX_RETRIES=str(args.max_tx_retries),
                UET_MSG_SIZE=str(args.size), UET_NUM_ITERATIONS=str(args.iterations),
                UET_IFNAME='vpp0', UET_VPP_SEGMENT=state['segments'][side],
                UET_PEER_IDENTITY=f'{peer_namespace}:15:{(peer_namespace << 22) | 16}',
                UET_VPP_DMA_SOCKET=str(Path(state['runtime']) / side / 'uet-dma.sock'),
                UET_VPP_IPV4_ADDR='198.18.0.1' if side == 'a' else '198.18.0.2')
            role, peer = ('client', '198.18.0.2') if side == 'a' else ('server', '198.18.0.1')
            with (directory / (role + '.log')).open('w') as out:
                process = subprocess.Popen([binary, role, 'untag', peer], env=env,
                    stdout=out, stderr=subprocess.STDOUT)
            processes.append(process)
            if side == 'b':
                time.sleep(0.2)
        outcome = 'completed'
        next_sample = 0
        while True:
            statuses = [process.poll() for process in processes]
            now = time.monotonic() - started
            if all(status is not None for status in statuses):
                break
            if any(status not in (None, 0) for status in statuses):
                outcome = 'application_error'
                break
            if now >= args.deadline_s:
                outcome = 'deadline_exceeded'
                break
            if now >= next_sample:
                sample_uet(root, state, directory, now, samples)
                next_sample = now + args.sample_ms / 1000
            time.sleep(0.02)
        if outcome != 'completed':
            sample_uet(root, state, directory, time.monotonic() - started, samples)
        for role, process in zip(('server', 'client'), processes):
            if process.poll() is None:
                terminated.append(role)
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        client_rc, server_rc = processes[1].returncode, processes[0].returncode
        if outcome == 'completed' and (client_rc or server_rc):
            outcome = 'application_error'
        elapsed = time.monotonic() - started
        if args.background_pps:
            cli(root, state, 'b', 'packet-generator disable-stream background')
            time.sleep(0.1 + args.reverse_buffer_ms / 1000)
        snapshot(root, state, directory, 2 * args.trace_packets + 20)
        warnings = {}
        for role in ('client', 'server'):
            log = re.sub(r'\x1b\[[0-9;]*m', '', (directory / (role + '.log')).read_text())
            warnings[role] = sum('WARN:' in line and 'retransmit' in line for line in log.splitlines())
        report = {'case': case, 'application': 'uet_demo', 'nsim_command': setup, 'drop_fraction': args.loss,
            'pulse_ms': args.pulse_ms, 'iterations': args.iterations, 'size': args.size,
            'pds': 'pds', 'timeout_ms': args.tx_timeout_ms, 'max_tx_retries': args.max_tx_retries,
            'mtu': args.mtu, 'delay_ms': args.delay_ms, 'reverse_delay_ms': args.reverse_delay_ms,
            'reverse_nsim_command': reverse_setup, 'deadline_seconds': args.deadline_s,
            'reorder_fraction': args.reorder_fraction, 'reorder_delay_ms': args.reorder_delay_ms,
            'reverse_bandwidth_mbps': args.reverse_bandwidth_mbps,
            'reverse_buffer_ms': args.reverse_buffer_ms,
            'background_pps': args.background_pps,
            'background_policer_mbps': args.background_policer_mbps,
            'background_commands': background_commands,
            'outcome': outcome, 'terminated_by_harness': terminated, 'live_samples': samples,
            'trace_mode': 'manual' if args.manual_trace else 'automatic',
            'trace_packets_per_input': None if args.manual_trace else args.trace_packets,
            'client_rc': client_rc, 'server_rc': server_rc, 'wall_seconds': elapsed,
            'pds_retransmit_warning_lines': warnings, 'results': str(directory)}
        (directory / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        if outcome != 'completed' or client_rc or server_rc:
            raise SystemExit(1)
    finally:
        if args.background_pps:
            stop_background(root, state, args.background_policer_mbps)
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if args.pulse_ms:
            nsim(root, state, args.loss, delay_ms=args.delay_ms)
        print('Logs: ' + str(directory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('up')
    sub.add_parser('down')
    command = sub.add_parser('cli')
    command.add_argument('side', choices=('a', 'b'))
    command.add_argument('command', nargs='+')
    console = sub.add_parser('console', help='Open the native interactive VPP CLI')
    console.add_argument('side', choices=('a', 'b'))
    case = sub.add_parser('run')
    case.add_argument('--loss', type=float, default=0.0)
    case.add_argument('--pulse-ms', type=float, default=0.0)
    case.add_argument('--size', type=int, default=4096)
    case.add_argument('--iterations', type=int, default=1000)
    case.add_argument('--mtu', type=int, default=1500, help='IPv4 egress MTU on both VPPs')
    case.add_argument('--delay-ms', type=float, default=0.1, help='A-to-B nsim delay in milliseconds')
    case.add_argument('--reverse-delay-ms', type=float, default=0,
                      help='B-to-A nsim delay in milliseconds (0 disables the reverse feature)')
    case.add_argument('--reorder-fraction', type=float, default=0,
                      help='Fraction sent through the A-to-B late-reorder wheel')
    case.add_argument('--reorder-delay-ms', type=float, default=1)
    case.add_argument('--reverse-bandwidth-mbps', type=float, default=1000)
    case.add_argument('--reverse-buffer-ms', type=float, default=0,
                      help='Enable B-to-A queued serialization with this buffer time')
    case.add_argument('--background-pps', type=float, default=0,
                      help='Ordinary UDP background traffic generated by VPP B')
    case.add_argument('--background-policer-mbps', type=float, default=0,
                      help='Policer Mbit/s rate on pg0 only; UET uses its own ingress')
    case.add_argument('--tx-timeout-ms', type=int, default=5)
    case.add_argument('--max-tx-retries', type=int, default=100)
    case.add_argument('--deadline-s', type=float, default=90,
                      help='Whole-run deadline; interrupted applications are recorded explicitly')
    case.add_argument('--sample-ms', type=int, default=500, help='Live show uet sampling interval')
    case.add_argument('--trace-packets', type=int, default=2000,
                      help='Bounded trace budget per input node and VPP worker (default: 2000)')
    case.add_argument('--manual-trace', action='store_true',
                      help='Preserve counters and trace settings configured in the VPP CLI')
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.action == 'up':
        up(args.root)
    elif args.action == 'down':
        down(args.root)
    elif args.action == 'cli':
        print(cli(args.root, read_state(args.root), args.side, ' '.join(args.command)), end='')
    elif args.action == 'console':
        prefix, _, env = paths(args.root)
        socket = Path(read_state(args.root)['runtime']) / args.side / 'cli.sock'
        raise SystemExit(subprocess.call([str(prefix / 'bin/vppctl'), '-s', str(socket)], env=env))
    else:
        if (not 0 <= args.loss <= 1 or args.pulse_ms < 0 or args.iterations < 1
                or args.size < 1 or not 1 <= args.trace_packets <= 20000
                or not 68 <= args.mtu <= 9000 or not 1 <= args.tx_timeout_ms <= 10000
                or not 1 <= args.max_tx_retries <= 1000 or not 50 <= args.sample_ms <= 10000
                or not math.isfinite(args.delay_ms) or not 0 < args.delay_ms <= 500
                or not math.isfinite(args.reverse_delay_ms) or not 0 <= args.reverse_delay_ms <= 500
                or not math.isfinite(args.deadline_s) or not 1 <= args.deadline_s <= 300
                or (args.loss and args.pulse_ms)):
            parser.error('Invalid scenario setting: loss, timing, MTU, tracing, iterations or message size')
        if (not math.isfinite(args.reorder_fraction) or not 0 <= args.reorder_fraction <= 1
                or not math.isfinite(args.reorder_delay_ms) or not 0 < args.reorder_delay_ms <= 100
                or not math.isfinite(args.reverse_bandwidth_mbps)
                or not 0 < args.reverse_bandwidth_mbps <= 100000
                or not math.isfinite(args.reverse_buffer_ms) or not 0 <= args.reverse_buffer_ms <= 100
                or not math.isfinite(args.background_pps) or not 0 <= args.background_pps <= 100000
                or not math.isfinite(args.background_policer_mbps)
                or not 0 <= args.background_policer_mbps <= 100000
                or (args.background_pps and not args.reverse_buffer_ms)
                or (args.background_policer_mbps and not args.background_pps)):
            parser.error('Invalid reorder, queue or background traffic setting')
        run_case(args.root, args)


if __name__ == '__main__':
    main()
