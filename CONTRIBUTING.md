# Contributing

Open an issue before changing control semantics, supported operating modes, or
protocol assumptions. Keep changes small and preserve the integration domain
and existing device/entity identities.

Fork this repository and create a branch from `master`. Match the existing code
style, add focused offline regression tests, and update related documentation.
Contributions use the repository's [MIT license](LICENSE).

## Offline development

Use an isolated environment, never the live Home Assistant environment.

| Home Assistant | Python | Purpose |
| --- | --- | --- |
| 2026.10.2 | 3.14.6 | Compatibility baseline |
| 2026.10.3 | 3.14.9 | Newer-runtime regression coverage |

The minimum supported HA version remains 2026.10.2. Python patch releases can
change runtime behavior; upgrading HA without matching the tested Python patch
is not equivalent coverage.

```bash
uv venv .venv --python 3.14.6
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Test the newer pair separately:

```bash
uv venv .venv-current --python 3.14.9
uv pip install --python .venv-current/bin/python -r requirements.txt
uv pip install --python .venv-current/bin/python homeassistant==2026.10.3
.venv-current/bin/python -m pytest -q
.venv-current/bin/ruff check .
```

Standard `venv` and `pip` also work. If uv's download catalog lacks a patch
release, supply an existing matching interpreter path with `--python` rather
than silently testing an older version.

Tests use real HA classes and loopback WebSocket servers. Platform forwarding
is mocked; they do not represent an installed-HA or hardware-control test.
The transport guard rejects non-loopback endpoints. Do not weaken it to test a
real fireplace.

## Protocol reference

The local endpoint is `ws://<host>:<port>/`, normally port 88. The known text
message vocabulary is:

| Message | Direction | Purpose |
| --- | --- | --- |
| `PROFLAMECONNECTION` | Client to dongle | Handshake and initial-state request |
| `PROFLAMECONNECTIONOPEN` | Dongle to client | Handshake acknowledgement |
| `PROFLAMEPING` | Client to dongle | Keepalive |
| `PROFLAMEPONG` | Dongle to client | Keepalive acknowledgement |

The client sends a keepalive every five seconds. Flat JSON state uses named
fields; temperatures are tenths of the selected unit (`0` Celsius, `1`
Fahrenheit). Preserve legitimate zeros, missing values, partial updates, and
unknown fields. Identical snapshots must not cause repeated entity updates,
but recognized traffic still counts toward connection liveness.

Setup/reconnect requires an acknowledgement and valid `main_mode` and
`temperature_unit`. This establishes protocol readiness, not measurement
freshness, physical operation, or permission to actuate.

New config entries obtain their identity automatically from DHCP discovery.
Manual host setup uses HA's public `async_discovered_service_info` lookup,
matching the resolved IP to one valid MAC and normalizing it with `format_mac`.
The DHCP integration is a declared dependency. Missing, invalid, or conflicting
identity records are setup errors, not reasons to invent an identity or ask the
user to enter one. No ARP/network probing is used for identification.
Reconfiguration and existing entries keep their stored identities unchanged.

### Keepalive and multi-message initial state

Keep `PROFLAMEPING` as an application text message; ordinary RFC 6455 ping/pong
is a different mechanism. Automatic WebSocket pings are disabled. The client
uses a monotonic receive-silence deadline on recognized application traffic and
bounded sends to recover stalled sessions. A successful transport pong does not
extend that application deadline. Do not replace the application keepalive based
on a single successful control-frame probe.

The [Control4 driver](https://github.com/psaab/proflame_c4/blob/3564607f00da49c91d1b52bac7434669b6b11f76/src/driver.lua)
similarly sends application pings and reconnects after three silent keepalive
intervals; inbound application frames reset its watchdog. Its timer tests also
cover cancellation of pending reconnects during teardown and late inbound
traffic. The [published probe findings](https://github.com/psaab/proflame_c4/blob/3564607f00da49c91d1b52bac7434669b6b11f76/tools/probes/FINDINGS.md)
report one successful RFC 6455 pong, while the later driver's comments report
disconnects with control-frame keepalive. Neither establishes a universal
control-ping contract. Do not run that repository's characterization script as
a read-only test: it sends setting changes.

Those findings describe five initial text messages containing 85 indexed pairs
over 79 distinct keys. The [Python mock server](https://github.com/Pharrox/proflame-wifi/blob/799ce824ec5f293212aeb614ade9cf0d4d956809/tests/mock_fireplace.py)
instead sends an ACK followed by one JSON message per field. These are reasons
not to equate one JSON message with a complete initial dump. Continue merging
late fields after readiness; do not wait for a fixed frame count or a quiet
period. PONGs alone must not satisfy or extend the initial-state deadline.

The [Python connection manager](https://github.com/Pharrox/proflame-wifi/blob/799ce824ec5f293212aeb614ade9cf0d4d956809/src/proflame_wifi/connection.py)
provides useful loopback-test precedent and periodic application sends, but
does not implement a receive-silence deadline. Do not copy its retained queue or
in-flight send item across reconnects. Our independent mock tests cover multiple
messages, WebSocket fragmentation/reassembly, late ACKs, incomplete initial
state despite PONGs, application silence despite transport pongs, and cleanup.
They do not run either upstream client or any hardware probe.

Control4 also implements a separate, configurable full-status refresh
(`PROFLAMECONNECTION`), defaulting to five minutes, distinct from keepalive.
Its source motivates this with no unsolicited frames in a ten-second idle
window. Idle silence alone does not demonstrate that an RF-remote change is
missed; local-panel actions, RF actions, and change-free periods must be tested
separately. This is polling precedent, not evidence that this integration needs
that interval or that a refresh forces a new thermometer sample.

Unknown indexed values retain their original decoded JSON type, including
numeric-looking strings, leading zeros, and nulls. Only recognized numeric
fields are converted for entity use. This preserves firmware diagnostics without
discarding unrelated valid state or turning unknown fields into HA entities.

### Read-only enforcement

New entries and legacy entries missing `read_only` default to observation-only
mode. Manual setup, DHCP confirmation, and Reconfigure expose the boolean.
Only explicit `false` permits editing; malformed types are rejected.
`set_state` checks read-only before queuing. Higher-level Heat/preset operations
also check it before branches that could otherwise silently do nothing.

Keep all existing entities and identities in either mode. Visible controls do
not grant permission to write: direct actions and automation calls must receive
a clear HA error. Read-only clients continue to receive state and use handshake
and keepalive messages; they must not send configuration or takeover writes.
Changing mode reloads the entry rather than replaying queued operations.

### Attended controller comparison

Sanitized findings from one Regency installation, not universal protocol rules:

| Scenario | Observed result |
| --- | --- |
| User RF lamp 1 to 2 on an existing subscription | Physical brightness change and pushed state without another handshake |
| Wi-Fi lamp writes during RF-initiated operation | Single-field echoes followed by remote-style full snapshots restoring the previous level; no sustained physical change |
| Wi-Fi device Off during RF-initiated burning | Off echo reverted to Manual in about 259 ms; burner remained burning, requiring RF Off |
| Official app Off during RF-initiated burning | Same flat command; Off echo reverted in about 23 ms; app showed Off but burner kept burning, requiring RF Off |
| RF Off followed by Wi-Fi Manual On | Mode persisted; user observed CPI and lamp operation without main-burner ignition when configured flame was zero |
| Wi-Fi lamp level 6 after Wi-Fi-initiated Manual On | User confirmed maximum brightness; reported setting persisted through about 50 seconds of observation |
| Official app Off after app-initiated operation | User confirmed shutdown and persistent reported Off in that test window |

Keep physical observations separate from command echoes. Source radio traffic
was not captured, so repeated full-state cadence plus reversions supports RF
reassertion without proving its internal mechanism. Remote presence alone is
not enough to classify ownership. Do not turn these findings into a speculative
takeover detector, retry loop, or unattended shutdown guarantee.

An AP capture of official-app reconnects showed the handshake followed by
`{"control0":"led_conf","value0":"0"}` and
`{"control0":"not_keep_ls","value0":"0"}`. Their semantics are unknown; app Off
still failed under RF control after these messages. Do not copy them as a
takeover fix. Passive captures were TCP-reassembled and duplicate AP-interface
packets removed before decoding masked WebSocket frames. Raw packet captures
contain private metadata and must not be committed.

The raw memory field reached `32678` with the main burner user-observed off and
CPI on, including after shutdown-related intermediate values. This contradicts
treating it as confirmed main-burner combustion. It remains internal and unknown.

### Undocumented fields and third-party observations

The [Hearthstone protocol documentation](https://github.com/taco8226/Hearthstone/blob/d40d9f6a2824c504d2d994dd7688003e034182b8/README.md#protocol)
reports `sh_memory_1=32672` for burner out, `32678` for burning, and other values
while lighting. A read-only capture correlated `32678` with user-observed
operation; later attended evidence above also observed it with the main burner
off. This is not a universal or validated combustion mapping. The
[Kotlin client](https://github.com/taco8226/Hearthstone/blob/d40d9f6a2824c504d2d994dd7688003e034182b8/app/src/main/java/com/tacorian/fireplace/data/ProflameClient.kt)
classifies every non-null, non-32672 value as burning. Do not copy that shortcut.
Keep raw values internal, leave unfamiliar/missing states unknown, and do not
derive gas usage, runtime, or confirmed heating from this hypothesis.

The newer-firmware Control4 capture also contains `burner_status="32672"` in
indexed traffic; Pharrox's mock defaults to `burner_status=32678` in flat JSON.
The former is recorded traffic, the latter synthetic test data, and neither
supplies attended combustion correlation for this integration. Together with
the `sh_memory_1` observation, these suggest a firmware-dependent representation,
not a validated alias or a universal numeric mapping. Keep both fields raw and
unclassified, including other numbers, strings, missing values, and nulls.

That client also flags suspected RF-remote control after three post-initial
messages containing at least eight keys within five seconds. Reported traffic
is about two full snapshots per second. This is a cadence heuristic, not a
control-ownership acknowledgement; other clients or firmware can produce
similar traffic. Do not add automatic takeover or retry-until-success writes.

Hearthstone attributes room temperature to the handheld remote. Its client
leaves initial age unknown but timestamps later messages containing temperature,
including unchanged snapshots. Receipt time is not a proven physical sample
time. Its [notification service](https://github.com/taco8226/Hearthstone/blob/d40d9f6a2824c504d2d994dd7688003e034182b8/app/src/main/java/com/tacorian/fireplace/notify/FireplaceService.kt)
uses a 15-minute freshness-based thermostat shutoff policy that depends on its
burner inference. That policy is not implemented here and must not be treated
as a manufacturer interlock.

Third-party whole-degree temperature/rounding observations and the inherited
indexed-format mode-5 envelope also need device-specific validation. Protocol
research must distinguish observations, hypotheses, and verified contracts.

## Staged hardware validation

1. **Offline:** run the tests and lint. Test serialization without a socket.
2. **Read-only:** with approval, use a bounded subscription, such as 30 seconds,
   sending only the handshake and five-second keepalives. Record messages and
   timings. A snapshot has no verified thermometer sample age.
3. **Attended controls:** obtain explicit approval for each group of setting
   writes, with someone present, physical controls available, and manufacturer
   instructions followed. User operation through the RF remote is not permission
   for software-issued writes.

Record starting conditions and time-aligned physical observations. Test one
approved setting at a time. Establish climate Off versus device Off, lamp/fan
behavior, thermostat operation, and state echoes without assuming a reported
setting confirms physical operation. Do not restore a stale snapshot, replay
commands after reconnect, deliberately induce faults, bypass interlocks, flash
firmware, or change networking as part of these tests.

For RF-remote propagation, start with a stable initial reading and leave the
same WebSocket subscription open. During separately approved, attended physical
remote adjustments, record whether changed fields arrive unsolicited, their
latency, and whether they are partial updates or repeated snapshots. Do not
reconnect or request status during this observation window: that would confound
push delivery with initial-state retrieval. The mock-server test of this path
proves client handling, not hardware push behavior.

Application PONGs and WiFi signal strength do not establish settings or
thermometer freshness. Only if pushes are missed should a separately approved
read-only comparison with another `PROFLAMECONNECTION` request be considered.
A returned snapshot is still not proof of a new physical temperature sample.
Do not add periodic polling, takeover writes, or retries merely because a value
is unchanged.

### Read-only RF capture tool

[`scripts/observe.py`](scripts/observe.py) prepares a continuous observation
window without installing anything into HA. Do not run it until an attended
experiment is agreed. Supply the endpoint explicitly and choose a new log file
outside the repository:

```bash
.venv/bin/python scripts/observe.py \
  --url 'ws://<host>:88/' \
  --output '/path/outside/the/repository/rf-capture.jsonl' \
  --duration 120
```

The destination directory must already exist. The tool creates an owner-only
JSONL file, refuses to overwrite an existing capture, sends one handshake, and
then sends only five-second application keepalives. It never sends setting
changes, requests another snapshot, or reconnects. It records exact received
text (or binary bytes as hex), UTC timestamps, monotonic elapsed times, and
session-end/error events. Logs can still contain private device metadata;
redact them before sharing.

The duration is capped at five minutes, plus bounded connection cleanup. A
missing acknowledgement, thirty seconds without application messages, or a
disconnect ends the capture with an error. Ctrl+C closes the socket and records
cancellation. The log is evidence, not an automated pass/fail verdict about
RF propagation, measurement freshness, or combustion.

For the first experiment:

1. Agree the physical action and starting conditions with the attending user.
   Do not begin with a mode, thermostat, pilot, or burner test.
2. Start one capture and establish a quiet baseline with initial state and
   continuing application replies. Observe for about thirty seconds before any
   adjustment.
3. Record the UTC time immediately before and after one agreed RF-remote
   adjustment, plus the user's physical observation. If appropriate and
   explicitly approved, a lamp-level adjustment avoids deliberately changing
   the burner setting.
4. Leave the same subscription open for another thirty seconds. Do not send
   app controls, refresh status, reconnect, or restore old settings automatically.
5. Stop or let the capture expire, then compare raw fields and timing with the
   reported action window. Missing replies or a disconnect make the window
   inconclusive, not proof that RF changes are never pushed.

No hardware connection is needed to validate this tool: its tests run against
loopback mock servers and assert the exact benign send vocabulary.

For a future burner-state hypothesis test, capture raw values across approved
user-operated off, ignition, steady operation, shutdown, and thermostat
idle/re-light states. Unobserved values must remain unclassified. Any writes to
test controller conflicts or temperature policy require separate approval.

Before sharing evidence, redact network addresses, hostnames, hardware
identifiers, credentials, and unrelated installation information.

## HACS distribution

This is a custom integration, not an integration in Home Assistant core.
The runtime belongs entirely in `custom_components/proflame_connect_wifi/`.
Keep that domain unchanged.

The repository uses release ZIP distribution. The release workflow sets the
archive's manifest version and packages the integration files and MIT license
at the archive root as `proflame_connect_wifi.zip`, matching `hacs.json`.
The default branch is hidden from HACS downloads; a Git tag alone is not a
downloadable release. Non-release commits must not produce an empty version or
an archive.

### Release gate

Pushing `master` runs validation but does not publish a release. Publication
requires manually running **Release** on `master` with **confirm_publish**
checked. Both HA/Python test pairs and HACS/Hassfest validation must pass in that
run before the publishing job starts. HACS checks the triggering commit SHA,
not a prior release. There are no ignored HACS checks.

Versions are explicit SemVer in the checked-in integration manifest, without a
`v` prefix or build metadata. The prepared evaluation version is `1.1.0-beta.2`.
The workflow checks out the exact validated commit, reads that version, verifies
the ZIP manifest matches, and creates tag `v1.1.0-beta.2` and its GitHub release
at that commit. Versions with a prerelease suffix are marked as GitHub
prereleases; HACS users may need to enable beta versions to select them.

To prepare another release, bump the manifest in a reviewed commit on `master`,
push it, and validate it before manually publishing. A tag that already exists
is an error, not permission to overwrite a release. Keep inherited tags
unchanged; do not backfill old releases that lack current safeguards or build a
historical tag with files from current `master`.

Publication is separate from approval to push preparation changes. Do not
manually dispatch Release just to test CI; use Lint and Validate instead.

Branding already exists for this integration domain in the
[Home Assistant brands repository](https://github.com/home-assistant/brands/tree/master/custom_integrations/proflame_connect_wifi).
HACS supports that fallback when local brand assets are absent. Keep the
domain stable. The README references the same registered product branding;
third-party marks are not part of the MIT code license. Do not copy arbitrary
app-store images or introduce manufacturer endorsement claims.

### Before the first public release

- Confirm GitHub Issues remain enabled and relevant repository topics are set.
  The manifest must point to this fork's documentation and issue tracker.
- Confirm the existing registered brand fallback passes HACS validation. If
  adding local assets later, follow
  [Home Assistant's brand guidance](https://contributors.home-assistant.io/core/integrations/brands/)
  and establish appropriate rights.
- Run both offline runtime pairs, distribution tests, HACS validation, and
  Hassfest. Resolve findings; do not present an ignored check as a pass.
- Complete the staged hardware validation, or clearly label an evaluation
  release with the remaining untested behavior.
- Publish a GitHub release with the matching ZIP asset. Check its root layout,
  manifest version, license, installation, upgrade, and unload behavior in an
  approved test HA instance before recommending it to users.

GitHub repository settings and publication are separate from local file changes.

### Default HACS catalog

Custom-repository installation and default-catalog inclusion are different.
Do not claim that the integration is listed until it is accepted. HACS requires
a public repository, metadata, working issues, branding, a published release,
and successful HACS/Hassfest checks without ignores before a default-catalog
submission.

Official references:

- [Integration repository requirements](https://www.hacs.xyz/docs/publish/integration/)
- [Repository metadata and HACS manifest](https://www.hacs.xyz/docs/publish/start/)
- [HACS validation action](https://www.hacs.xyz/docs/publish/action/)
- [Default-catalog submission](https://www.hacs.xyz/docs/publish/include/)
- [HA integration manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)
