# Contributing

Open an issue before changing control behavior, supported modes, or protocol
assumptions. Fork the repository, create a branch from `dev`, and submit a
focused pull request into `dev` with tests and any necessary documentation
updates. `dev` holds unreleased changes; `master` receives reviewed releases.

Follow the existing code style. Preserve the integration domain and existing
device and entity identities. Contributions use the [MIT license](LICENSE).

## Development setup

Use an isolated environment, not your running Home Assistant installation.
Keep editor settings and any local Home Assistant configuration out of Git.
CI tests these compatibility targets on pushes to `dev` and `master` and on
contributor pull requests into `dev`. Release PRs from `dev` to `master` use
the existing `dev` push checks, avoiding duplicate runs. The release workflow
validates the merged commit again before publication.
Weekly checks run on the default branch, `master`:

| Home Assistant | Python |
| --- | --- |
| Minimum supported: 2026.10.2 | Latest 3.14 patch |
| Latest stable from PyPI | Latest 3.14 patch |

Install dependencies and run the baseline checks:

```bash
uv venv .venv --python 3.14
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Test latest stable separately:

```bash
uv venv .venv-current --python 3.14
uv pip install --python .venv-current/bin/python -r requirements.txt
uv pip install --python .venv-current/bin/python --upgrade homeassistant
.venv-current/bin/python -m pytest -q
.venv-current/bin/ruff check .
```

Standard `venv` and `pip` also work. CI logs the installed HA and Python versions;
the latest target excludes prereleases. Passing tests establish offline
compatibility, not verified hardware behavior.

Update CI's Python target when HA changes its required Python minor version;
pip otherwise selects the newest HA compatible with the older interpreter.

The integration uses HA's shared `aiohttp` session and has no additional runtime
requirements. `websockets` is a development-only dependency for loopback mock
servers; it is not imported by the integration or the observation script.
Dependabot opens weekly development dependency and GitHub Actions updates into
`dev`. Test both compatibility targets when updating dependencies.

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

### Prereleases from dev

Finish the changes on `dev`, then bump
`custom_components/proflame_connect_wifi/manifest.json` in a separate commit,
for example to `1.0.0-beta.4`. Pushing that version change to `dev` runs both HA
test targets, HACS validation, and Hassfest before publishing a prerelease.
Normal commits and contributor PR merges without a version change do not publish.

### Stable releases from master

When ready for a stable release, remove the prerelease suffix on `dev` in a
separate preparation commit, for example `1.0.0`. That commit deliberately fails
the dev publication gate: stable versions cannot publish from `dev`.
Open a `dev` to `master` PR with the included changes and upgrade notes. Wait
for the four required CI checks and review, then use a merge commit, not squash
or rebase. The version change on `master` runs release validation and publishes
the stable release. Prerelease versions cannot publish from `master`.

Keep `dev` and merge `master` back into it before starting the next cycle.
The next development prerelease must use a new version, such as `1.0.1-beta.1`.

Use SemVer without a `v` prefix or build metadata. Verify the published tag,
release notes, and `proflame_connect_wifi.zip`. The ZIP must contain the runtime
files and license at its root, with a manifest matching the tagged source.
Beta notes cover changes since the previous release; stable notes include all
changes since the previous stable release. The first stable release starts from
the original upstream baseline rather than listing inherited history.

Other manifest edits do not trigger publication. The trigger compares versions
on pushes to `dev` or `master`; do not bypass stable-release review.
The workflow packages the exact validated commit and refuses an existing tag.
Keep historical tags unchanged and do not backfill old releases with current
files.

A manually confirmed Release dispatch on either release branch is available for recovering
an interrupted release, with the same checks and tag protection. Use Lint or
Validate for standalone CI checks; do not dispatch Release just to test it.

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
