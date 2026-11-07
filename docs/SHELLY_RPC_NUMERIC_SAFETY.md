# Shelly RPC numeric field safety

All supported Shelly models are read-only voltage monitoring
sources. Their RPC payloads are untrusted input, not evidence
that any numeric value is physically real.

Earlier, the single-phase and three-phase clients used
float() directly. A string NaN or Infinity could become a
non-finite voltage/frequency. In the Pro 3EM client, a NaN
phase reached SQLite and caused a NOT NULL integrity error
after other phases had potentially already been written.

The candidate now uses one numeric validation function at the
RPC adapter boundary for Shelly Pro EM-50, EM Gen3, PM Mini,
1PM variants and Pro 3EM/3CT63:
- Reject NaN, positive or negative Infinity, and boolean values.
- Reject negative voltages/frequencies, which cannot be
  meaningful magnitudes in these meter fields.
- Keep valid finite numeric strings compatible with RPC APIs.
- Preserve a real device-reported zero without manufacturing
  extra zero rows. This patch does not fabricate a voltage.
- Reject an entire Pro 3EM atomic response if a present phase
  contains invalid numeric fields, rather than persisting a
  partial misleading set of phase readings.
- Avoid embedding untrusted payload text in exception messages.

The tests are entirely local with mocked RPC responses and
temporary SQLite. Physical Pro 3EM/3CT63 acceptance remains
unverified until a real device is installed and the operator
allows testing. Production stays on 0.5.13-hw and is unchanged.
