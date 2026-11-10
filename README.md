# Explore and troubleshoot UET with VPP

A software-only lab for observing Ultra Ethernet Transport (UET) in VPP's
native counters and packet traces, then injecting network conditions to
explain the reference transport's behavior.

**[Read the hands-on article](docs/article.md)** ·
**[Download the complete article and lab](https://github.com/jtollet/uet-vpp-lab/releases/download/v1.0.3/uet-vpp-article-and-lab.zip)** ·
**[Download just the lab scripts](https://github.com/jtollet/uet-vpp-lab/releases/download/v1.0.3/uet-vpp-observability-lab.zip)**

Two UET application processes exchange packets with two VPP instances over
shared memory. A virtual memif link joins the VPPs. UET terminates in the
applications; VPP provides forwarding, observation and controlled impairments.

The five experiments cover:

- Packet loss and retransmission, correlated by packet sequence number.
- A path MTU mismatch that blocks larger messages.
- Delayed feedback and retries before an ACK returns.
- Reordered original requests without nsim packet loss.
- A congested return queue, followed by ingress policing of competing UDP traffic.

## Run the lab

Use Ubuntu Linux with at least four available logical CPUs and sudo access.
Eight GiB RAM is a reasonable starting allocation. All source dependencies
are public and pinned in the [lab README](lab/README.md).

```sh
sudo apt-get update
sudo apt-get install -y git build-essential python3
git clone https://github.com/jtollet/uet-vpp-lab.git
cd uet-vpp-lab/lab
export UET_LAB_ROOT="$HOME/uet-observability"
UET_LAB_INSTALL_DEPS=1 bash build.sh "$UET_LAB_ROOT"
sudo python3 lab.py --root "$UET_LAB_ROOT" up
sudo python3 lab.py --root "$UET_LAB_ROOT" run --iterations 20
```

For the repeated reorder and return-queue comparisons:

```sh
sudo python3 transport_scenarios.py --root "$UET_LAB_ROOT" --repeats 3
sudo python3 lab.py --root "$UET_LAB_ROOT" down
```

The article and lab README explain native CLI commands, the intentionally
failing MTU case and the cleanup required after a failed application.

## Validation and scope

The source versions are VPP 26.10, the public UEC reference implementation
and libfabric 1.20.1. The application calls the reference `uet_*` API directly;
libfabric provides types and support functions. This lab uses no verbs API.
The transport library and NIC shim remain unchanged. The supplied application
addressing and trace-instrumentation patches are described in the lab README.

On 10 November 2026, Florin Coras reviewed and merged the
[nsim wheel-departure tracing enhancement](https://gerrit.fd.io/r/c/vpp/+/47531)
into VPP `master`. The validated lab remains on VPP 26.10, so its companion
keeps the version-specific adaptation. See the
[upstream status and patch scope](lab/README.md#upstream-status--10-november-2026).

Validation includes 15 x86-64 executions of the extended scenarios, including
a comparison with applications restricted to the same four CPUs as VPP.
The added scenarios were also exercised in an existing ARM64 Linux VM.
The complete dependency installation, build and 13 scenario executions were
also validated in a fresh Ubuntu 24.04.5 LTS x86-64 VM with four virtual CPUs,
eight GiB RAM and a 40 GiB disk. No extra manual dependency installation was
needed. See the [fresh-VM validation report](evidence/fresh-vm-validation.json).

The exercises demonstrate reliable untagged messaging in ROD mode and the
effect of network conditions. They do not validate all UET delivery modes,
security, multipath or the endpoint congestion-control algorithms. The QoS
example limits background ingress; it is not a strict-priority output scheduler.
The reordered run's `No Match` and `Bad Generation` responses trigger
message-level recovery, independently of the PDS retransmission warning count.
See the [trace and source explanation](evidence/reorder-recovery.md).

## Evidence and downloads

- [Selected loss, MTU and latency CLI output](evidence/vpp-cli-debugging-results.txt).
- [Selected reorder and QoS CLI output](evidence/vpp-cli-transport-results.txt).
- [Extended x86-64 measurements](evidence/transport-validation-x86-20261109.json).
- [Final source and buffer-ownership audit](evidence/transport-final-audit.json).
- [Loss and baseline evidence](https://github.com/jtollet/uet-vpp-lab/releases/download/v1.0.3/uet-vpp-native-cli-evidence-20261108.zip).
- [MTU and latency evidence](https://github.com/jtollet/uet-vpp-lab/releases/download/v1.0.3/uet-vpp-debugging-evidence-20261108.zip).
- [Reorder and QoS evidence](https://github.com/jtollet/uet-vpp-lab/releases/download/v1.0.3/uet-vpp-transport-evidence-20261109.zip).

Published evidence is sanitized as described in [PRIVACY.md](PRIVACY.md).
Protocol sequence numbers, counters, timing measurements and application
outcomes are retained. The nine PCAP files contain synthetic background
generator input, rather than captured user traffic.

## Licenses

See [LICENSE](LICENSE), [NOTICE](NOTICE) and [LICENSES](LICENSES) for the MIT
and Apache-2.0 terms applicable to these files and upstream-derived patches.
