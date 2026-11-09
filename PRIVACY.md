# Publication privacy review

The published tree was prepared from an explicit allowlist of lab scripts,
documentation, patches and experiment evidence. It contains no original
development-repository history or member-portal material.

The evidence archives were rebuilt after the following processing:

- Home-directory paths and build identities replaced with generic lab values.
- Machine names replaced with a generic lab hostname.
- Nonzero application-owner process identifiers replaced with placeholders;
  zero remains zero to preserve clean-state observations.
- Runtime memory addresses replaced with placeholders.
- Raw packet hexdump lines omitted from CLI traces and embedded JSON strings.
- ZIP metadata rebuilt without extra fields or archive comments.

Protocol sequence numbers, transport context identifiers, counters, graph
timestamps, measured intervals and application return codes were preserved.
The nine PCAP files in the transport evidence are identical, synthetic UDP
generator inputs. Each was checked against the expected frame and payload;
these are not packet captures of user traffic.

The prepared files were checked with Gitleaks 8.30.1, including recursive
archive inspection and decoding. A separate recursive review checks personal
identities, email addresses, home paths, internal hostnames and network
addresses, member-only URLs, credential URLs, private-key headers and
unexpected binary files. See the machine-readable `privacy-report.json` for
the final results.

The public account name appears in the GitHub repository and download URLs
needed to distribute this lab. Commit attribution uses a generic contributor
identity and a non-personal GitHub no-reply address. Upstream organizational
copyright and license notices remain intact.

At the author's explicit request, the article acknowledgments name Eric Davis
in his public upstream role. This intentional public credit contains no
contact details or private correspondence.

These checks describe the published artifacts. New experiment runs naturally
generate local paths, process identifiers and runtime details on the reader's
own machine; review new evidence before sharing it.
