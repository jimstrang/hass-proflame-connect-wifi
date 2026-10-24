# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (using `scripts/lint`).
4. Test you contribution.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose); it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People *love* thorough bug reports. I'm not even kidding.

## Use a Consistent Coding Style

Use [black](https://github.com/ambv/black) to make sure the code follows the style.

## Test your code modification

This custom component is based on [integration_blueprint template](https://github.com/ludeeus/integration_blueprint).

It comes with development environment in a container, easy to launch
if you use Visual Studio Code. With this container you will have a stand alone
Home Assistant instance running and already configured with the included
[`configuration.yaml`](./config/configuration.yaml)
file.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.

## Read-only RF capture tool

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
