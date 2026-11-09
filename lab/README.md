# Explore and troubleshoot UET with VPP 26.10

This functional lab connects two VPP processes through memif and runs a UET
SEND/RECV test at each end. VPP provides routing, graph tracing, interface and
UET-plugin counters, decoded UET headers in native graph traces, and packet
loss and reorder injection through `nsim`. A second experiment creates a
shared return-path queue and uses VPP's policer to limit background traffic.
The UET transport terminates in the application processes.

All dependencies come from public upstream repositories. No private UEC
repository, physical NIC, PCI binding, QEMU guest, or custom kernel is used.
The application calls the implementation's `uet_*` API directly. This is not
a libfabric-provider or libibverbs tutorial.

## Versions

| Component | Public source | Pinned revision |
| --- | --- | --- |
| VPP | https://github.com/FDio/vpp | `v26.10`, `4acb320269bc4f3533e35c80afedac38853b2f60` |
| UET implementation, VPP plugin and NIC shim | https://github.com/ultraethernet/uet-ref-prov | `5b4e0491329b8e28764bca6847e2f6a0eaa84b9b` |
| Libfabric types and support functions | https://github.com/ofiwg/libfabric | `v1.20.1`, `e43589a5113ad3db0a18a6a1a02b6e6f4ee317d6` |

VPP 26.10 was the latest stable release when this lab was validated on
8 November 2026. The VPP runtime, SDK and external UET plugin are built
together; no SDK from a different VPP release is used.

## Requirements

Use x86-64 Linux with at least four available logical CPUs and `sudo`.
Eight GiB RAM is a reasonable starting allocation. Validation used Ubuntu
26.04.1, Linux 7.0.0-38, with VPP pinned to four CPU cores. The 1% loss case
also passed with the applications restricted to those same four cores.
The added reorder and QoS scenarios were also exercised in an existing
ARM64 Lima VM (six logical CPUs, four GiB RAM). That VM completed the clean,
reordered and policed cases; its persistent-overload case exhausted the retry
budget. A fresh minimal-VM package installation has not been validated.

Build tooling and dependencies can be installed using VPP's official
`install-dep` target, as shown below. These commands install packages;
the compiled libraries and executables are installed only under the lab root.

## Build

Run the build as a regular user. The optional dependency installer uses sudo.

```sh
sudo apt-get update
sudo apt-get install -y git build-essential python3
export UET_LAB_ROOT="$HOME/uet-observability"
UET_LAB_INSTALL_DEPS=1 bash build.sh "$UET_LAB_ROOT"
```

On a host with the build dependencies already installed, omit
`UET_LAB_INSTALL_DEPS=1`. Use `JOBS=4` to set build parallelism explicitly.

## The example client

The official application `uet.c` constructs its peer address with fixed
identity defaults, including PIDonFEP 0. The VPP shim assigns its application
a nonzero namespace and PIDonFEP. Without exchanging or configuring that
identity, the original application receives `Msg Rsp: Undeliverable`.

`build.sh` therefore creates a separate `uet_demo` application from the pinned
official `uet.c`, applying the supplied `peer-identity.patch` to a generated
copy. It adds one application-only environment option:

```text
UET_PEER_IDENTITY=PIDonFEP:ResourceIndex:InitiatorID
```

The launcher supplies the peer's VPP namespace and the single-endpoint
defaults from this pinned implementation. For the first segment on each
fresh VPP, the identity is `1:15:4194320`. This example supports one endpoint
and one application segment per VPP. It is not a general address discovery
service.

The official checkouts, UET transport library and NIC shim remain unmodified.
The companion supplies three explicit additions, applied to generated copies:

- `peer-identity.patch`: application-only peer addressing, as described above;
- `vpp-pds-trace.patch`: bounded header snapshots and PDS decoding in the UET
  plugin's trace formatter;
- `nsim-trace.patch`: distinguish simulated drops from queued packets and
  record departure from the nsim wheel in a copy of VPP's nsim plugin.

`build.sh` builds the UET and nsim copies under `uet-trace-plugin-build`.
Its plugin directory precedes VPP's stock plugin directory. It also builds and
loads the upstream `tracepath` and `policer` plugins. The additions are included openly in
this archive; they are not claimed to be merged upstream. They change trace
instrumentation, not the transport algorithms or the loss model.

## Start

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" up
```

The launcher creates two VPP processes, private CLI/API names and application
segments, and a memif link. Each VPP uses a main thread and one worker. It uses
ordinary pages rather than requiring preconfigured hugepages. The endpoints
are `198.18.0.1/30` and `198.18.0.2/30`; static neighbors keep ARP out of the
loss experiments. No Linux network namespace or physical interface is changed.

## Observe

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" cli a show uet
sudo python3 lab.py --root "$UET_LAB_ROOT" cli a show interface
sudo python3 lab.py --root "$UET_LAB_ROOT" cli a show errors
sudo python3 lab.py --root "$UET_LAB_ROOT" cli a show nsim
```

Replace `a` with `b` to inspect the other endpoint.

## Run the three experiments

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" run --loss 0
sudo python3 lab.py --root "$UET_LAB_ROOT" run --loss 0.01
sudo python3 lab.py --root "$UET_LAB_ROOT" run --pulse-ms 20
```

Each run executes 1,000 ping-pong iterations in contiguous-buffer mode and
1,000 more in IOV mode using the `untag` test. Every message is 4,096 bytes.
The server checks buffer-mode payloads; the client checks the echoed IOV data. The sender and receiver must
both exit successfully. `--iterations` and `--size` change these parameters.

The launcher selects `UET_PDS=pds`. Defaults are a 5 ms retransmission timeout
and a budget of 100 retries; `--tx-timeout-ms` and `--max-tx-retries` override them. These are explicit lab settings, not recommendations for a
production fabric. The larger retry budget permits the short outage to recover.
`UET_PKT_DROP_THRESH=0` disables loss injection inside the UET stack.

The VPP A output feature uses the following commands. The loss probability
applies to packets, not complete application messages; it affects data and
acknowledgements sent from A to B.

```text
set nsim delay 100 us bandwidth 1 gbit packet-size 1500 drop-fraction 0.01
nsim output-feature enable-disable memif1/0
```

The one-shot outage starts 1 ms after the first packet reaches the loss model:

```text
set nsim delay 100 us bandwidth 1 gbit packet-size 1500 drop-once after 1 ms for 20 ms
```

The baseline retains the same configured delay and bandwidth with
`drop-fraction 0`. Configure the model before starting traffic; changing an
active `nsim` configuration can discard packets held in its scheduling wheel.

## Read the results

Each run prints the result directory and writes:

- `report.json`: application exit codes, scenario outcome, settings, total
  wall time, live-sample paths and counts of retransmission warning lines;
- `live/*/a-show-uet.txt` and `live/*/b-show-uet.txt`: periodic snapshots while
  the applications are attached;
- `client.log` and `server.log`: application progress, data validation outcome
  and PDS warnings;
- `a-show-*.txt` and `b-show-*.txt`: counters, state, traces and runtime output;
- `a-show-trace-paths.txt` and `b-show-trace-paths.txt`: summaries of the
  observed graph paths, using VPP 26.10 tracepath.

Use the named `Network loss simulation drop packet` counter to identify
injected losses. `show errors` also contains activity counters such as
`Packets buffered` and `Packets transmitted`; their presence does not mean
the transfer failed. `show uet` checks the application/VPP boundary, including
invalid requests, ring saturation and outstanding RX buffers.

## Troubleshoot from the native VPP CLI

Open a console in a separate terminal for each VPP:

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" console a
sudo python3 lab.py --root "$UET_LAB_ROOT" console b
```

The commands below run at `vpp#`. Disable paging before pasting command groups:

```text
set terminal pager off
show version
show plugins
show threads
show memif
show interface memif1/0
show interface address
show ip fib
show ip neighbors
```

Expect memif `admin-up connected`, the correct endpoint IP, a route through
memif and a static neighbor. Use `quit` to close a console; the VPP process
continues running until `lab.py down`.

The default `run` clears counters and arms 2,000 packets per input node per
worker. To configure the trace explicitly, execute this in both consoles:

```text
clear trace
clear errors
clear interfaces
clear uet counters
trace add uet-input 2000
trace add memif-input 2000
```

Then run in the shell, preserving that manual configuration:

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" run --loss 0.01 --manual-trace
```

Inside VPP A, inspect the impairment and the observed paths:

```text
show interface features memif1/0
show nsim
show errors
show trace paths
show trace path 3 max 1
show trace max 4000
```

Select the actual path index from `show trace paths`; `3` is the drop path
in the supplied 1% sample, not a stable index. Its trace ends at
`nsim-output-feature`, with `NSIM: dropped, simulated network loss`.
`nsim` frees these dropped buffers directly, so an `error-drop` trace filter
would miss the injected loss.

The extended UET trace prints direction, source/destination IP, PDS type,
RETX, SPDCID, DPDCID (or PDC-info on SYN), and PSN; ACKs display CACK_PSN.
Match source IP, SPDCID and PSN to correlate the original request and retry.
On ACKs, RETX means acknowledgement of a retransmitted packet; count reliable
requests when looking for retransmissions. VPP's local `id` is not a wire PSN.
The supplied `correlated-retransmission.txt` shows PSN `505542063` being
injected, dropped, retransmitted, and delivered by `uet4-ip-input` on VPP B.

Only traced packets incur the bounded 96-byte header snapshot and formatter
work. Encrypted/security headers, RUDI layouts, IPv4 fragments and IPv6
extensions are reported without speculative decoding. The tracepath summary
contains the nodes that emitted trace records, not every internal graph node.

```text
show uet
show runtime
show buffers
```

`show uet` covers the application/VPP boundary: invalid requests, saturated
rings, invalid RX releases and outstanding RX buffers. After successful exit,
`provider-ready no` and `provider-owner-pid 0` are expected, as is
`rx-outstanding 0`. VPP still reserves its DMA slot buffers; a nonzero `Used`
value in `show buffers` is not itself a leak. `show runtime` reports per-node
activity by thread, not application RTT or a transport retry counter.

Traces are bounded to 2,000 packets at each of `uet-input` and `memif-input`
per worker by default; `--trace-packets` changes that budget. Stored snapshots
request `show trace max 4000` for this default budget, since plain `show trace`
displays only 50 packets per thread. Each run clears the previous automatic
traces but retains its text files. Re-arm tracing after a budget is exhausted.
Scheduling and trace overhead can cause retries without an injected loss.
This diagnostic lab is not a throughput or latency benchmark.

Optional formatter checks, including every truncated-header length and
IPv4/native and IPv6/UDP wire vectors, run with AddressSanitizer and UBSan:

```sh
python3 check_trace_format.py "$UET_LAB_ROOT"
```

## Validation on 8 November 2026

| Experiment | Message size | Iterations per mode | Injected drops on A | Client / server exit |
| --- | --- | --- | --- | --- |
| No injected loss | 4,096 bytes | 1,000 | 0 | 0 / 0 |
| 1% packet loss, manually armed traces | 4,096 bytes | 1,000 | 99 | 0 / 0 |
| One 20 ms outage | 4,096 bytes | 1,000 | 37 | 0 / 0 |

The exact random-loss count is not an assertion for future runs. All cases
passed with data checking. The native VPP trace confirmed the original drop,
RETX request and delivery for the same SPDCID/PSN. This revised recipe performs
no PCAP capture and requires no packet-analysis application. The earlier
recipe also passed a 64-byte baseline and a four-CPU-restricted 1% run; those
additional cases were not rerun for this instrumentation revision.

## Debugging scenarios: use VPP to identify and verify the cause

Every run reapplies the requested MTU and nsim configuration before starting
applications. Defaults are IPv4 MTU 1500, 100 us forward delay, no reverse
nsim feature, zero loss, and a 5 ms PDS timeout. The launcher checks the actual
`show interface features` output to avoid repeated feature registrations and
to verify removal of the reverse feature. A stored nsim model is distinct
from an enabled output feature.

### A small probe passes, a larger message fails

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" run --mtu 576 --size 64 --iterations 20
sudo python3 lab.py --root "$UET_LAB_ROOT" run --mtu 576 --size 4096 --iterations 1 --deadline-s 8
```

The second command is an expected failed experiment. In the validated run,
UET generated 1,108-byte IPv4 packets with DF set. VPP counted 404 `ip4 MTU
exceeded and DF set` events. `show uet` reported 404 accepted TX submissions,
404 TX slot completions, zero invalid requests, and zero UET RX deliveries.
The client emitted 400 retransmit warnings, exhausted the PDS retry budget,
reported a CQ I/O error and exited with code 255. The waiting server was
terminated by the harness (recorded explicitly as `server`, return code -15).
That return code does not describe an independent transport failure.

Use `show interface memif1/0`, `show errors`, `show trace paths` and a selected
`show trace path INDEX max 1`. The packet length, DF bit, rewrite MTU 576 and
`ip4-icmp-error` path localize the failure before memif output. The app's
configured shim MTU remains 1500; this is an intentional path/config mismatch.

Recreate the lab after a failed application, then restore the MTU:

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" down
sudo python3 lab.py --root "$UET_LAB_ROOT" up
sudo python3 lab.py --root "$UET_LAB_ROOT" run --mtu 1500 --iterations 20
```

This applies `set interface mtu ip4 1500 memif1/0` on both VPPs. The corrected
transfer passed both modes with no retry warnings or MTU drops. Reopen native
CLI consoles after restarting VPP.

### Retransmissions without a network drop

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" run --delay-ms 20 --reverse-delay-ms 20 --tx-timeout-ms 5 --iterations 20
sudo python3 lab.py --root "$UET_LAB_ROOT" run --delay-ms 20 --reverse-delay-ms 20 --tx-timeout-ms 100 --iterations 20
sudo python3 lab.py --root "$UET_LAB_ROOT" run --delay-ms 0.1 --reverse-delay-ms 0 --tx-timeout-ms 5 --iterations 20
```

Both VPP nsim models report no loss. In the short-timeout case, a UET request
was retried before its original ACK returned about 40 ms after the first
send, using the same VPP's trace clock. Source IP, SPDCID and PSN/CACK_PSN
correlate the records. This distinguishes delayed feedback from actual
injected loss. The test completed, and boundary/ring counters stayed clean.

| Setting, same 20 iterations per mode | VPP A TX packets | Client warning lines | Result |
| --- | --- | --- | --- |
| 20 ms each way, 5 ms timeout | 2763 | 2560 | Both applications completed |
| 20 ms each way, 100 ms timeout | 203 | 0 | Both applications completed |
| 100 us forward, no reverse feature, 5 ms timeout | 203 | 0 | Both applications completed |

The 100 ms setting uses the existing library environment option; no libuet
code is changed. It is an experimental control, not production tuning advice.
The third row verifies that removing the VPP-injected delay eliminates the
retries with the original timeout. Periodic `show uet` samples show increasing
TX/RX and receive-release counts while the applications remain attached.

`show uet` TX completions describe the packet-I/O boundary, not a wire ACK.
Use decoded PDS traces and the application's outcome to establish transport
progress. Warning lines are not distinct PSNs. The CLI evidence and complete
logs for all these cases are included in the debugging evidence archive.

### Reorder packets without deliberately dropping them

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" run --iterations 30
sudo python3 lab.py --root "$UET_LAB_ROOT" run --iterations 30 \
    --reorder-fraction 0.25 --reorder-delay-ms 1
```

Both commands keep the same 100 us forward propagation delay, zero random
loss and 5 ms retransmission timeout. The second command places selected
packets on nsim's late-reorder wheel for up to one additional millisecond.
The application still exercises ROD; this is not a RUD/SACK comparison.

Inspect `show nsim`, `show errors`, and native traces on both VPPs. Confirm
that the reordered counter advances and that neither simulated-loss nor
queue-overflow drops explain the result. On B, follow the arrival order of
original `ROD_REQ` packets (`RETX 0`) within the same source/SPDCID, then check
that both applications complete their data checks. On A, each packet's
`nsim-wheel` timestamp records its actual release; traces themselves are
listed by trace allocation, not sorted by wheel-release time.

### Congest the return path, then limit its background traffic

These matched runs retain a 40 Mbit/s nominal queued link on B, with a 20 ms
buffer and 100 us propagation delay. The first has no background traffic,
the second adds ordinary UDP generated by VPP B, and the third limits that
same background ingress through VPP's policer:

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" run --size 64 --iterations 30 \
    --reverse-delay-ms 0.1 --reverse-bandwidth-mbps 40 --reverse-buffer-ms 20
sudo python3 lab.py --root "$UET_LAB_ROOT" run --size 64 --iterations 30 \
    --reverse-delay-ms 0.1 --reverse-bandwidth-mbps 40 --reverse-buffer-ms 20 \
    --background-pps 5000
sudo python3 lab.py --root "$UET_LAB_ROOT" run --size 64 --iterations 30 \
    --reverse-delay-ms 0.1 --reverse-bandwidth-mbps 40 --reverse-buffer-ms 20 \
    --background-pps 5000 --background-policer-mbps 6
```

VPP B's packet generator injects IPv4/UDP through `pg0` on worker 0. Routing
puts it on the same memif output and nsim wheel as UET's return packets.
The generator replays a single frame written by the launcher using Python's
standard library. This PCAP file is a generator input, not a packet capture;
all diagnostic observations come from VPP's native CLI and application logs.
The generator has a finite safety limit and is stopped when the test ends.

The QoS policy applies only to background ingress on `pg0`. UET arrives from
its separate shared-memory ingress, so its transport ACKs and replies do not
pass through that policer. This demonstrates load control through policing;
it does not implement a strict-priority output scheduler. Excess background
packets are deliberately dropped before reaching the shared queue.

The queued nsim model serializes every packet using its configured nominal
`packet-size 1500`, including small UET packets. Thus 40 Mbit/s represents
about 3,333 nominal packets per second in this model. These settings make a
functional experiment visible on an ordinary computer; they are not a model
of a production fabric's rate or a measurement of its latency.

In B's native CLI, inspect:

```text
show packet-generator verbose
show interface pg0
show interface features pg0
show interface features memif1/0
show policer name background
show nsim verbose
show errors
show uet
show trace paths
```

The launcher saves policer state before detaching the temporary policy.
For UET packets, compare the interval from `uet-input` to `nsim-wheel` within
B's trace. Both ACKs and echoed data share this return path. Correlate an ACK's
`CACK_PSN` with the sender's request/retry PSN, and distinguish nsim queue-full
drops from the intentional background drops in `policer-input`.

### Collect comparable native evidence

```sh
sudo python3 transport_scenarios.py --root "$UET_LAB_ROOT" --repeats 3
```

The lab must already be running. The collector executes a clean and a
reordered run, then repeats the three return-queue settings above three times.
Use `--scenario reorder` or `--scenario qos` to select one family.
It retains every launch log and saves a JSON summary pointing to the full
native CLI snapshots and application logs. It also selects reordered arrival
pairs and return-path trace intervals; these cover the sampled traces, not
every packet in the run. A persistent-overload application failure is recorded explicitly, and the
collector recreates the isolated lab before the QoS repair run. Other failures
stop collection with the evidence intact. Recreate the lab before retrying.

### Extended validation on 9 November 2026

The x86-64 evidence includes 15 executions: the clean/reordered pair, three
matched quiet/loaded/policed trios, a further trio with the applications
restricted to the same four CPUs as the VPP threads, and one policed CLI
capture under that restriction. All 15 completed both application modes.
Every run uses 30 iterations per mode; the reorder pair uses 4,096-byte
messages and the return-queue cases use 64-byte messages.

| Setting (three main x86 runs each) | Median traced ACK wait on B | Client PDS retry warnings | nsim queue-full drops on B |
| --- | --- | --- | --- |
| Queued return link, no background | 0.401–0.402 ms | 0 | 0 |
| Same queue, 5,000 UDP packets/s | 20.034–20.098 ms | 626–1,337 | 12,529–27,302 |
| Same traffic, 6 Mbit/s background policer | 0.402–0.403 ms | 0 | 0 |

In the first policed run, `show policer` recorded 1,675 conforming background
packets and 15,208 violating packets. Thus QoS admitted background traffic
while discarding excess traffic before it reached nsim. A final `show uet`
audit on both VPPs found zero invalid requests, full-ring events, invalid RX
releases, outstanding RX buffers and application-owner PIDs. The public
VPP, UET and libfabric checkouts were unchanged; the isolated lab was stopped.

The reordered x86 run sent 100 packets through the late wheel and recorded
43 backward steps in original ROD request PSNs in B's sampled receive order.
Neither side recorded nsim drops, and both application logs contained zero
PDS retransmission warnings. The client did log 12 `No Match` and 12 `Bad
Generation` semantic responses before completing its data checks. These are
not counted as PDS timeout/retransmit warnings, and successful completion
should not be described as an absence of all semantic-layer responses.

Read the counter Reason column carefully: `show errors` truncates some long
labels, including `Packets dropped due to lack of spac`. The collector
recognizes that displayed prefix as the queue-full reason. In the congestion
case, the random-loss model remains `none` while queue-overflow drops occur.
On A, background UDP packets with their separate test source are discarded
by `ip4-local` with `ip4 source lookup miss`; this is the background sink,
not a UET receive failure. Their destination is A's local address, and their
source has no local receive-source route. The background does not enter libuet.

The exact counts and overloaded outcome depend on scheduling. In the ARM64
VM the same continuous overload exhausted the retry budget; clean and policed
controls completed with no PDS retry warnings. The automated collector
records an overloaded application failure and recreates this isolated lab
before its QoS repair control. A deadline interruption remains a harness
failure and stops the collector.

The full CLI snapshots, live samples and application logs are in
`uet-vpp-transport-evidence-20261109.zip`; selected readable output is in
`vpp-cli-transport-results.txt`. Summary intervals are measured between
`uet-input` and `nsim-wheel` within VPP B's graph timestamps. They cover only
sampled traces, include the simulated propagation delay, and are not
end-to-end application RTTs.

### Scenario options

| Option | Default | Meaning |
| --- | --- | --- |
| `--mtu` | `1500` | IPv4 egress MTU configured on both VPPs |
| `--delay-ms` | `0.1` | Forward A-to-B nsim delay |
| `--reverse-delay-ms` | `0` | Reverse delay; zero removes the B-to-A feature unless a reverse buffer is requested |
| `--reorder-fraction` | `0` | Fraction sent to the forward late-reorder wheel |
| `--reorder-delay-ms` | `1` | Maximum extra reorder delay |
| `--reverse-bandwidth-mbps` | `1000` | Nominal bandwidth of B's nsim model |
| `--reverse-buffer-ms` | `0` | Reverse queue depth in milliseconds; zero selects the fixed-delay model |
| `--background-pps` | `0` | Ordinary UDP packets generated per second on B |
| `--background-policer-mbps` | `0` | Policer Mbit/s rate for background ingress; zero leaves it unpoliced |
| `--tx-timeout-ms` | `5` | Existing UET PDS retransmission-timeout setting |
| `--max-tx-retries` | `100` | Existing UET PDS retry budget |
| `--sample-ms` | `500` | Interval between live `show uet` snapshots |
| `--deadline-s` | `90` | Whole-run bound including application initialization |

The report distinguishes `completed`, `application_error` and
`deadline_exceeded`. `terminated_by_harness` identifies processes stopped
by the launcher. After failure, stop and recreate the lab before continuing.
A deadline interruption is not a UET protocol result.

## Stop

```sh
sudo python3 lab.py --root "$UET_LAB_ROOT" down
```

Only the VPP processes recorded for this lab are stopped. Results are retained
in the printed runtime directory. After an interrupted application run, stop
and recreate the lab before another experiment, so stale shared-buffer
ownership is not reused.
