# Runtime mode ownership

The MG24 firmware is authoritative for its physical runtime mode. A completed BLE
write means only that the command reached the GATT write operation; it does not
prove that the firmware consumed or executed the command.

The gateway tracks four independent values:

- `connection_status`: BLE transport state (`connected`, `backoff`, and so on).
- `requested_mode`: the operator's target (`LIVE` or `LOW_POWER`), if unresolved.
- `actual_mode`: the latest current sensor evidence (`LIVE`, `LOW_POWER`, or
  `UNKNOWN`). A disconnect makes this `UNKNOWN`; it does not imply low power.
- `transition_state`: request progress (`*_REQUESTED`, `*_PENDING`,
  `*_CONFIRMED`, or `*_FAILED`).

After a mode command is written, the transition becomes pending while
`actual_mode` remains unchanged. A matching `mode_live` or `mode_low_power`
acknowledgement proves that the firmware accepted/executed the command and is
shown separately by `transition_acknowledged`. Current, non-replayed telemetry
or heartbeat with `rm=live` or `rm=low_power` confirms the sustained physical
mode and completes the transition. Persistent-journal replay intentionally omits
`rm`; RAM-buffered records are marked `d=1` on disconnect or mode change. The
gateway never uses delayed records as current physical-state evidence.

`LIVE_NEXT_WAKE` remains requested until a low-power wake is observed. The
gateway then writes `MODE LIVE` and keeps the request pending until current
telemetry confirms `rm=live`. Write failure, disconnect, or missing confirmation
cannot clear the request or manufacture a LIVE state.

The firmware owns the ten-minute LIVE safety timeout and returns its rails to
LOW_POWER locally. The gateway tracks an expected deadline for display only; it
does not send a competing timeout command and waits for firmware telemetry to
confirm the resulting LOW_POWER state.
