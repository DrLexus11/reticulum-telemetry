# reticulum-telemetry

The gateway, backend and dashboards for the mesh's board health reports
(PR F step F4 of `microReticulum_Firmware/docs/TAKDeliveryPlan.md`). **Public
repository.**

## Rules

- **The wire format is the firmware's.** `gateway/telemetry_codec.py` and
  `tests/fixtures/telemetry_v1.json` are copies of the firmware repository's;
  change them there first, copy here the same week, and keep
  `tests/test_codec_fixture.py` passing.
- **Nothing operational is committed:** no hashes, identities, credentials,
  coordinates or callsigns. Gateway identities live outside the repository.
- **Secrets never on a command line.** Broker credentials come from the
  environment.
- **Loopback by default.** Every service in `deploy/` binds 127.0.0.1; exposing
  one is a deliberate change with its own authentication.
- **Absent is not zero.** A field a board does not report is `null` in JSON and
  a missing series in Prometheus, never a 0 that draws as a fault.
- Push to `origin` (DrLexus11) only; branch, pull request, Copilot's review, one
  fix round, merge.
