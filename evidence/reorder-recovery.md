# Reordering and message-level recovery

The recorded x86-64 reordered run completed 30 exchanges in each buffer mode.
Its client logged 12 `Msg Rsp: No Match` and 12 `Msg Rsp: Bad Generation`
messages, while the server logged 12 `RX: Unexpected Message` and 12
`RX: Bad Generation` messages. Both applications exited with status zero.
The PDS retransmission warning count was zero on both sides. That count
does not include message-level (SES) restarts.

## What the pinned source does

All links below refer to the tested public UEC revision, with no transport
or NIC-shim changes:

- The [server example](https://github.com/ultraethernet/uet-ref-prov/blob/5b4e0491329b8e28764bca6847e2f6a0eaa84b9b/uet.c#L2729)
  posts one receive, waits for it, echoes the message, then waits for the
  echo's send completion before posting the next receive.
- The [receive path](https://github.com/ultraethernet/uet-ref-prov/blob/5b4e0491329b8e28764bca6847e2f6a0eaa84b9b/uet_api.c#L4092)
  returns `No Match` when an untagged SEND arrives with no posted receive.
  In ROD mode it also disables the endpoint's untagged generation.
- [Posting a new receive](https://github.com/ultraethernet/uet-ref-prov/blob/5b4e0491329b8e28764bca6847e2f6a0eaa84b9b/uet_api.c#L6105)
  increments and re-enables that generation. A request carrying the earlier
  generation then receives `Bad Generation`.
- The [sender response handler](https://github.com/ultraethernet/uet-ref-prov/blob/5b4e0491329b8e28764bca6847e2f6a0eaa84b9b/uet_api.c#L5270)
  schedules a delayed message retry for `No Match`. For `Bad Generation`
  it updates the peer generation and schedules another message attempt.
  These responses take the retry path rather than the fatal-error path.

## What the VPP trace establishes

Selected events in **VPP B's own clock**, from the published reordered trace:

| VPP B time | Event |
| --- | --- |
| 00:00:06:167950 | Last packet of the previous echo leaves toward A; PDC 66, PSN 1954782305 |
| 00:00:06:168073 | Next request arrives from A; PDC 65, PSN 1954782314 |
| 00:00:06:168225 | ACK for the previous echo arrives from A; PDC 66, cumulative ACK PSN 1954782305 |

The next request arrives 152 microseconds before the ACK that allows the
server to complete its previous send. These are different PDCs; ordering
within one ROD delivery context does not order this request against an ACK
for the reverse context. The application sequence and the paired logs
explain the temporary absence of a posted receive and the generation recovery.
VPP tracing records the packet order; the application logs and source explain
the SES response names. The lab's PDS formatter does not display SES statuses.

The packet and message retry mechanisms must therefore be reported separately.
This run demonstrates successful recovery under this workload; it does not
establish that reordering is free of retries or that every UET delivery mode
has been validated.
