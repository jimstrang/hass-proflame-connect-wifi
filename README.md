# Proflame Connect WiFi for Home Assistant

[![Proflame by SIT](https://raw.githubusercontent.com/home-assistant/brands/master/custom_integrations/proflame_connect_wifi/logo.png)](https://github.com/home-assistant/brands/tree/master/custom_integrations/proflame_connect_wifi)

A community-maintained Home Assistant integration for fireplaces with a SIT
Proflame WiFi dongle. It connects over your local network without a cloud
account. Install it through [HACS](https://www.hacs.xyz/) or copy the files
manually.

This is a custom integration, not part of Home Assistant core or an official
SIT or Regency product.

> **Experimental and read-only by default.** Tested on a Regency installation.
> Read
> [RF remote precedence](#rf-remote-precedence-and-shutdown-limitations)
> before enabling controls or creating automations.

## Requirements

- Home Assistant **2026.10.2 or newer**.
- A Proflame WiFi dongle with a local WebSocket endpoint, normally on TCP port
  **88**. Other ports can be configured manually. Compatibility depends on the
  fireplace and dongle firmware; Proflame controls alone do not guarantee it.
- Network access from Home Assistant to the dongle. A DHCP reservation or stable
  hostname is recommended.
- [HACS](https://www.hacs.xyz/docs/use/) for managed installation and updates,
  or manual installation as described below.

The tested endpoint uses unencrypted `ws://` without credentials. Keep it on a
trusted local network. Do not expose the dongle to the Internet through port
forwarding or a public reverse proxy.

## Installation

### HACS custom repository

This repository is not in the default HACS catalog. Add it as a custom
repository to download published releases.

Open this repository in HACS:

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jimstrang&repository=hass-proflame-connect-wifi&category=integration)

Or add it manually:

1. Open **HACS** in Home Assistant.
2. Select the three-dot menu, then **Custom repositories**.
3. Add `https://github.com/jimstrang/hass-proflame-connect-wifi` with type
   **Integration**.
4. Find **Proflame Connect WiFi**, select a release, and download it.
   For an evaluation version such as `1.1.0-beta.1`, use the version selector
   to choose the release marked as a prerelease, rather than a commit ID.
5. Restart Home Assistant during a suitable maintenance window.
6. Continue with [Configuration](#configuration).

### Manual installation

1. Download this repository.
2. Copy the
   [`custom_components/proflame_connect_wifi`](custom_components/proflame_connect_wifi/)
   directory into `<config>/custom_components/`.
3. Confirm that
   `<config>/custom_components/proflame_connect_wifi/manifest.json` exists.
4. Restart Home Assistant, then continue with [Configuration](#configuration).

Home Assistant installs the integration's runtime dependencies. Do not install
the development requirements into your running Home Assistant environment.

## Configuration

If HA discovers the dongle, select **Add** on its discovery card. Otherwise,
open **Settings > Devices & services > Add integration** and search for
**Proflame Fireplace**. Setup uses the UI, not YAML.

| Field | Value |
| --- | --- |
| Name | A friendly name for the fireplace |
| Host | The dongle's IPv4 address or resolvable hostname |
| Port | `88` unless your device uses another WebSocket port |
| Read-only mode | Enabled by default. Keep enabled to observe state without sending any setting changes. |

Setup checks the handshake and initial state without changing settings.
If setup reports that it cannot identify the device, check that HA's DHCP
discovery or a network integration reports the dongle, then retry. This
discovery information is required for initial setup even across VLANs.

### Read-only and editable modes

Read-only mode is enabled by default. Existing entries without this setting
also become read-only on upgrade, so review any control automations first:
their actions will fail until editing is explicitly enabled.

In read-only mode, the integration connects, receives state, and sends
application keepalives. It blocks setting writes for power, climate modes and
presets, temperature, flame, fan, lamp, and pilot. It does not shut down the
fireplace or stop the RF remote or other clients from controlling it.

Entities and their reported states remain available. Controls can still appear
in HA, but using one raises a read-only error without queuing a command.

To enable editing, open the entry's **Reconfigure** action and disable
**Read-only mode**. Re-enable it there to return to observation-only operation.
Changing the setting reloads the entry without automatically restoring fireplace
settings. Editable mode permits writes but does not take ownership, override the
RF remote, or make Off reliable.

### Changing the host or network

Use the integration entry's **Reconfigure** action to change the host or port.
Do not delete and recreate the integration just to change its address.

For routed or VLAN-separated networks, allow Home Assistant to initiate a TCP
connection to the dongle's configured port, including return traffic and any
required DNS resolution. DHCP discovery may not cross VLANs. Verify that the new
address belongs to the same dongle before reconfiguring it.

## Entities

The integration creates the following entities. It does not detect optional
hardware capabilities, so some may not apply to your fireplace.

| Platform | Function |
| --- | --- |
| Climate | Reported room temperature, target temperature in Thermostat/Smart mode, Heat/Off modes, and Off/Manual/Thermostat/Smart presets |
| Fan | Configured fan speed, levels 0-6, presented as a percentage |
| Light | Configured lamp brightness, levels 0-6, presented as HA brightness |
| Number | Configured flame height, levels 0-6; not a flame sensor |
| Select: Pilot mode (configuration) | Continuous pilot (CPI) / intermittent pilot (IPI) setting, not a pilot-flame sensor |
| Switch: Device power | Reported device operating-mode on/off, independent of the climate preset and configured flame height |
| Sensors (diagnostic, disabled by default) | Free heap, minimum free heap, and WiFi signal, when reported by the firmware |

### Device power and climate Off

Climate Off sets flame height to zero and selects Manual mode, unless the device
is already Off. Device power Off sends the device-Off command. Validate their
physical effects on your fireplace.

Device power shows On when the reported operating mode is nonzero, including
Manual with flame height zero. Climate can show Off at the same time.
Turning Device power on restores only the remembered operating mode, without
an extra flame-restoration command. Use climate Heat/Manual or the flame-height
control to request heat deliberately.

Mode-only On can still ignite the burner, depending on retained device
settings. Neither the power switch nor the climate entity confirms combustion.

### Flame level in Smart mode

Smart mode controls flame output automatically. The integration rejects direct
flame-slider adjustments, including zero, with an error while Smart is reported.
It sends no flame command and does not silently switch modes. Select Manual or
Thermostat before adjusting the configured level.

Climate Off, Device power Off, and preset changes remain available, subject to
the RF overwrite limitation below.

The flame-level value is a configured setting, not live flame-height telemetry.
During attended testing, Smart raised the physical flame while the reported
level stayed unchanged. The slider cannot confirm flame output or combustion.

## Behavior and limitations

### RF remote precedence and shutdown limitations

When the fireplace is started with the RF remote and the remote remains in
range, its settings can override Wi-Fi commands, including Off. Changes may
revert, and a reported Off state does not confirm physical shutdown. Editable
mode does not override the remote.

**Do not rely on a timer or presence automation to extinguish an RF-started
fireplace.** Use the normal RF controls to turn it off. Remote presence alone
does not necessarily block Wi-Fi control; behavior varies by device and firmware.
The integration has no automatic takeover, continuous retry, remote-disable,
or undocumented initialization writes.

### Other limitations

- Presets, operating mode, and configured flame height do not establish
  combustion. Heating action stays unknown. Undocumented fields
  (`sh_memory_1` and `burner_status`) remain internal until validated for the
  device; they are not used to calculate gas consumption or runtime.
- Room temperature is the last value the dongle reported. A live connection or
  repeated snapshot does not prove the thermometer has taken a fresh reading.
  This integration is not a safety controller or thermostat supervisor.
- An isolated RF lamp change reached the existing subscription without polling
  on the tested installation. The integration does not poll periodically, and
  keepalive replies do not mean it received a fresh settings or temperature
  snapshot.
- State follows device reports rather than optimistic updates. Even an echoed
  setting does not confirm physical burner, fan, or lamp operation.
- Explicit On actions can restore settings observed during the current client
  lifetime. Before any are observed, the defaults are Manual mode and level 6.
  Settings are not persisted across reloads.
- Fan, light, and flame entities show zero in device Off even if a nonzero
  setting is retained. Fan cooldown and lamp behavior while Off are unverified.
- The tested dongle uses flat JSON. Indexed-format support, including its mode-5
  command envelope, is retained but has not been validated on that dongle.
- Disconnects make entities unavailable and discard unsent commands. New
  commands require fresh acknowledged state; nothing is replayed on reconnect.
  Already transmitted commands cannot be recalled, and a multi-command
  operation can be interrupted.

Keep physical controls and manufacturer safety interlocks in use. Get explicit
approval from the person responsible for the fireplace before initial control
tests, and have someone present. Do not leave new fireplace automations unattended.

## Troubleshooting and support

### Cannot connect

Check the host, port, routing, firewall rules, and hostname resolution.
A reachable TCP port alone does not confirm the Proflame protocol; setup needs
an acknowledgement and valid initial state.

### No discovery card

Try manual setup. A discovery card is not required for operation, but initial
setup still needs a valid MAC record in HA's discovery data.

### Controls revert or return a read-only error

If settings revert, the RF remote or another client may be sending them.
Do not repeatedly send commands to fight another controller.

A read-only error means writes are disabled. This is the default, including
after upgrading an entry without an explicit mode setting. Use **Reconfigure**
to review the warning and enable editing only if appropriate.

### Sensors show unknown

Some firmware omits diagnostic fields, so missing readings stay unknown.
Optional diagnostic sensors are disabled by default for new entities; enable
them individually if your firmware supplies the data. Upgrades preserve your
enabled/disabled choices. You can manually disable any already-enabled sensors
that your device does not support.

### Temperature does not change

The dongle may be reporting a retained value. This alone does not indicate a
connection failure or prove that room temperature is constant.

For a bug report, enable debug logging from the integration's menu, reproduce
the problem safely, then disable logging. Include the integration and HA
versions, fireplace and dongle model/firmware if known, expected behavior, and
relevant logs. Remove IP addresses, hostnames, MAC addresses, credentials, and
unrelated device information before posting publicly.

Use this fork's [issue tracker](https://github.com/jimstrang/hass-proflame-connect-wifi/issues),
not the original project's tracker.

## Development and credits

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, tests, hardware-test
safety, and release instructions.

Based on work by [Pharrox](https://github.com/Pharrox/hass-proflame-connect-wifi)
and [jeffrapp](https://github.com/jeffrapp/hass-proflame-connect-wifi), with
selective fixes informed by
[jameskorzekwa](https://github.com/jameskorzekwa/hass-proflame-connect-wifi).
Protocol research also drew on
[psaab/proflame_c4](https://github.com/psaab/proflame_c4).
Distributed under the [MIT license](LICENSE).

The linked product logo comes from the Home Assistant brands repository.
Proflame and SIT names and marks belong to their respective owners and are not
covered by this repository's MIT license. They identify the supported product
without implying manufacturer endorsement.
