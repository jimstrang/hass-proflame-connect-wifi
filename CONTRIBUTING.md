# Contributing

Open an issue before changing control behavior, supported modes, or protocol
assumptions. Fork the repository, create a branch from `master`, and submit a
focused pull request with tests and any necessary documentation updates.

Follow the existing code style. Preserve the integration domain and existing
device and entity identities. Contributions use the [MIT license](LICENSE).

## Development setup

Use an isolated environment, not your running Home Assistant installation.
Keep editor settings and any local Home Assistant configuration out of Git.
CI tests these runtime pairs:

| Home Assistant | Python |
| --- | --- |
| 2026.10.2 | 3.14.6 |
| 2026.10.3 | 3.14.9 |

Install dependencies and run the baseline checks:

```bash
uv venv .venv --python 3.14.6
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Test the newer runtime separately:

```bash
uv venv .venv-current --python 3.14.9
uv pip install --python .venv-current/bin/python -r requirements.txt
uv pip install --python .venv-current/bin/python homeassistant==2026.10.3
.venv-current/bin/python -m pytest -q
.venv-current/bin/ruff check .
```

Standard `venv` and `pip` also work. If uv cannot download the required Python
patch release, pass the path to an installed interpreter with `--python`.
Testing an older Python patch does not cover the runtime listed above.

## Tests and pull requests

Add regression tests for behavior changes. Tests use HA classes and loopback
WebSocket servers; platform forwarding is mocked. They must run without a
fireplace or live HA instance. Keep the non-loopback connection guard enabled.

When changing the client or entities:

- Store each entry's coordinator in `ConfigEntry.runtime_data`; platforms use
  that coordinator and its client. Climate service inputs are already in the
  device's native unit, so encode them as tenths without another unit conversion.
- Preserve missing values, legitimate zeros, partial updates, and raw unknown
  fields. Keep unclassified fields out of entity state.
- Test initial-state handling across multiple messages, malformed input,
  disconnect/reconnect, availability, cancellation, and cleanup.
- Assert the exact serialized commands. Never replay pending commands after a
  disconnect.
- Enforce read-only access at the shared write boundary, including service calls
  and actions that would otherwise silently do nothing.
- Keep application keepalives separate from WebSocket control pings and status
  requests. A live connection does not establish measurement freshness.
- Use HA discovery data for new device identities. Do not invent hardware IDs,
  rely on ARP, or replace existing identities during reconfiguration.

Describe what changed, why it changed, and which checks you ran in the PR.
Distinguish offline test results from observations made on real hardware.
Do not include private addresses, identifiers, credentials, or raw captures.

## Hardware testing

Read the [behavior and limitations](README.md#behavior-and-limitations) before
testing controls. In particular, Wi-Fi Off is not a dependable way to stop
RF-initiated burning on the tested setup. A reported setting or command echo
does not confirm physical operation.

Test in this order:

1. Run offline tests and inspect command serialization.
2. Arrange a bounded read-only observation.
3. Obtain explicit approval for each attended control test, with someone present
   and normal physical controls available.

Record starting conditions, action times, and physical observations. Test one
approved setting at a time. Operating the RF remote does not authorize additional
software commands. Do not automatically restore old settings, fight another
controller with repeated writes, bypass interlocks, deliberately induce faults,
flash firmware, or change networking as part of a control test.

### Read-only capture

Use [`scripts/observe.py`](scripts/observe.py) for an agreed observation window:

```bash
.venv/bin/python scripts/observe.py \
  --url 'ws://<host>:88/' \
  --output '/path/outside/the/repository/rf-capture.jsonl' \
  --duration 120
```

Create the output directory first. The script creates a private JSONL file and
refuses to overwrite it. It sends one handshake and five-second application
keepalives, with no setting writes, refresh requests, or reconnects. Captures
stop at the duration limit (at most five minutes), on a missing acknowledgement,
after thirty seconds without application messages, or on disconnect. Ctrl+C
closes the connection.

Logs contain raw messages, UTC timestamps, and elapsed times. Redact private
metadata before sharing them, and keep raw captures out of Git.

## Releases

Keep code changes and release preparation separate. Feature and fix PRs should
not change the manifest version.

To release:

1. Open a separate release PR that bumps
   `custom_components/proflame_connect_wifi/manifest.json` and describes the
   included changes and upgrade notes.
2. Use SemVer without a `v` prefix or build metadata, for example
   `1.1.0-beta.3`. A prerelease suffix produces a GitHub prerelease.
3. Wait for both runtime test jobs, HACS validation, and Hassfest to pass.
4. Merge to `master`. The version change triggers the Release workflow, which
   runs validation again before publishing.
5. Verify the published tag, release notes, and `proflame_connect_wifi.zip`.
   The ZIP must contain the runtime files and license at its root, with a
   manifest matching the tagged source.

Other manifest edits do not trigger publication. The trigger compares versions
on pushes to `master`; do not bypass release review with a direct version bump.
The workflow packages the exact validated commit and refuses an existing tag.
Keep historical tags unchanged and do not backfill old releases with current
files.

A manually confirmed Release dispatch on `master` is available for recovering
an interrupted release, with the same checks and tag protection. Use Lint or
Validate for standalone CI checks; do not dispatch Release just to test it.

GitHub generates release notes from merged PRs, contributors, and a changelog
link. Include meaningful descriptions and upgrade notes in PRs.

## HACS packaging

Keep all runtime files under `custom_components/proflame_connect_wifi/`.
The release asset name and layout must match `hacs.json`. The default branch is
hidden from HACS downloads, so users need a published release, not just a tag.

The domain already has branding in the
[Home Assistant brands repository](https://github.com/home-assistant/brands/tree/master/custom_integrations/proflame_connect_wifi).
HACS supports that fallback. Third-party marks are not covered by the MIT code
license; establish appropriate rights before adding local artwork.

Keep repository metadata and issue links current. HACS and Hassfest must pass
without ignored checks. Test installation and lifecycle changes in an approved
test HA instance before recommending a release, or state the untested behavior
in its evaluation-release notes.

Custom-repository installation does not imply default-catalog inclusion. See
the official requirements before submitting:

- [Integration repository requirements](https://www.hacs.xyz/docs/publish/integration/)
- [Repository metadata and HACS manifest](https://www.hacs.xyz/docs/publish/start/)
- [HACS validation action](https://www.hacs.xyz/docs/publish/action/)
- [Default-catalog submission](https://www.hacs.xyz/docs/publish/include/)
- [HA integration manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)
