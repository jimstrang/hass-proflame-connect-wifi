# Proflame Connect WiFi for Home Assistant

A community-maintained custom integration for fireplaces with a SIT Proflame
WiFi dongle. It connects directly over your local network, without a cloud
account, and is intended for installation through [HACS](https://www.hacs.xyz/).
It is not a built-in Home Assistant integration or an official SIT or Regency
product.

> **Experimental, read-only by default.** Attended tests on one Regency
> installation verified RF state updates and selected Wi-Fi controls, but
> **Wi-Fi Off failed to stop RF-initiated burning**. The official iPhone app
> showed the same limitation. Installation into a running Home Assistant
> instance has not yet been validated for this fork. See
> [RF remote precedence](#rf-remote-precedence-and-shutdown-limitations) before
> enabling editing or planning automations.

## Requirements

- Home Assistant **2026.10.2 or newer**. Offline tests cover 2026.10.2 and
  2026.10.3; compatibility with older releases is not established.
- A Proflame WiFi dongle exposing the local WebSocket protocol on TCP port
  **88**, or a manually configured port. Compatibility varies by fireplace and
  dongle firmware; the presence of Proflame controls alone does not guarantee it.
- Network access from Home Assistant to the dongle. A DHCP reservation or stable
  hostname is recommended.
- [HACS](https://www.hacs.xyz/docs/use/) for managed installation and updates,
  or manual installation as described below.

The observed local endpoint requires no credentials and uses unencrypted
`ws://`. Keep it on a trusted local network. Do not expose the dongle to the
Internet through port forwarding or a public reverse proxy.

## Installation

### HACS custom repository

This repository is **not listed in the default HACS catalog**. Its packaging is
configured for release downloads; a published release of this fork is required
before this installation method is available. Until then, use manual
installation for development or evaluation.

Open this repository in HACS:

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jimstrang&repository=hass-proflame-connect-wifi&category=integration)

Or add it manually:

1. Open **HACS** in Home Assistant.
2. Select the three-dot menu, then **Custom repositories**.
3. Add `https://github.com/jimstrang/hass-proflame-connect-wifi` with type
   **Integration**.
4. Find **Proflame Connect WiFi**, select a release, and download it.
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

Open **Settings > Devices & services > Add integration**, then search for
**Proflame Fireplace**. Configuration is through the UI, not YAML.

| Field | Value |
| --- | --- |
| Name | A friendly name for the fireplace |
| Host | The dongle's IPv4 address or resolvable hostname |
| Port | `88` unless your device uses another WebSocket port |
| Unique ID | Prefer the dongle's lowercase MAC address, such as `aa:bb:cc:dd:ee:ff`. Do not use its IP address. |
| Read-only mode | Enabled by default. Keep enabled to observe state without sending any setting changes. |

Setup verifies the handshake and initial state without changing settings.
Supported DHCP discoveries are also checked before a discovery prompt appears;
manual configuration works when discovery is unavailable.

### Read-only and editable modes

**Read-only mode is the default, including for existing entries that do not yet
have this setting.** Upgrading such an entry disables its control writes until
editing is explicitly enabled. Existing automations that attempt controls will
receive an error; review them before upgrading.

Read-only mode still connects, subscribes to state, and sends application
keepalives. It blocks all setting writes, including power, climate modes and
presets, temperature, flame, fan, lamp, and pilot. It does not shut down the
fireplace or prevent the RF remote or other clients from controlling it.

Existing entities, identifiers, and reported states remain available. Controls
can still be visible in HA, but attempting to use them raises a clear read-only
error without queuing a command. Read-only mode is not implemented by simply
hiding controls.

To enable editing, open the entry's **Reconfigure** action and disable
**Read-only mode**. Re-enable it there to return to observation-only operation.
The entry reloads without changing device or entity identities and never
automatically restores settings. Editable mode permits writes; it does **not**
claim ownership of the fireplace, override the RF remote, or make Off reliable.

### Changing the host or network

Use the integration entry's **Reconfigure** action to change the host or port.
Keep the unique ID unchanged so existing entities and automations retain their
identities. Do not delete and recreate the integration just to change its address.

For routed or VLAN-separated networks, allow Home Assistant to initiate a TCP
connection to the dongle's configured port, including return traffic and any
required DNS resolution. DHCP discovery may not cross VLANs. Verify that the new
address belongs to the same dongle before reconfiguring it.

## Entities

Available controls depend on the fireplace and its reported state. The
integration currently creates these entities rather than discovering optional
hardware capabilities.

| Platform | Function |
| --- | --- |
| Climate | Reported room temperature, target temperature in Thermostat/Smart mode, Heat/Off modes, and Off/Manual/Thermostat/Smart presets |
| Fan | Configured fan speed, levels 0-6, presented as a percentage |
| Light | Configured lamp brightness, levels 0-6, presented as HA brightness |
| Number | Configured flame height, levels 0-6; not a flame sensor |
| Select | Continuous/intermittent pilot setting |
| Switch | Full-device operating-mode on/off |
| Sensors | Free heap, minimum free heap, and WiFi signal, when reported by the firmware |

**Climate Off and the power switch are different controls.** Climate Off
preserves the inherited behavior of setting flame height to zero and selecting
Manual mode, unless already device-Off. The power switch sends device Off.
Their physical effects require validation on the specific fireplace.

## Behavior and limitations

### RF remote precedence and shutdown limitations

Attended tests on one installation found that, after operation was initiated
with the RF remote and the remote remained in range, its settings repeatedly
overwrote Wi-Fi commands. The dongle pushed full snapshots roughly twice per
second. Lamp changes briefly echoed before reverting, and a Wi-Fi device-Off
command **did not physically shut down the burning main burner**.

The official iPhone app sent the same flat `{"main_mode":0}` command in the same
RF-initiated scenario. It displayed Off while the main burner continued burning;
RF Off was required to stop it. Do not mistake a transient Off echo or UI state
for physical shutdown.

After RF Off, the repeated snapshots stopped in the observed window. Wi-Fi
Manual On and a lamp change to maximum subsequently persisted, and the user
confirmed the lamp physically changed. The official app also successfully
switched off app-initiated operation. These results support RF setting
reassertion, not a universal rule that merely having the remote nearby blocks
Wi-Fi. The RF radio traffic itself was not captured; other devices and firmware
may behave differently.

**Do not rely on this integration to extinguish an RF-started fireplace after a
timer expires or everyone leaves home.** Editable mode does not bypass this
limitation. Keep normal physical controls available. No automatic takeover,
continuous retry, remote-disable, or undocumented initialization writes are
implemented.

### Other limitations

- **No confirmed burner telemetry.** Presets, operating mode, and configured
  flame height do not establish combustion. Heating action remains unknown.
  Undocumented burner-related fields (`sh_memory_1` and `burner_status`) stay
  internal pending device-specific validation; no gas-consumption or runtime
  sensors are derived from them.
- **Temperature freshness is unknown.** Room temperature is the last value
  reported by the dongle. A live connection or repeated snapshot does not prove
  a fresh thermometer reading. Do not use this integration as a safety controller
  or assume it supervises thermostat operation.
- **Push is not freshness.** An isolated RF lamp adjustment reached an existing
  subscription without polling on the tested installation. Periodic status
  polling is not implemented; a keepalive reply does not mean a new settings
  or temperature snapshot was obtained.
- **Settings are not physical-operation confirmation.** The integration waits
  for reported state rather than applying optimistic updates. A state echo still
  does not confirm that a burner, fan, or lamp is physically operating.
- **Restoration is limited.** Explicit on actions can restore settings observed
  during the current client lifetime. The inherited defaults before observation
  are Manual mode and level 6. These settings are not persisted across reloads.
- **Device-Off display is inherited.** Fan, light, and flame entities show zero
  in device Off even when a nonzero setting is retained. Fan cooldown and lamp
  operation while Off are not established.
- **Firmware variants differ.** Flat JSON is observed on the tested dongle.
  Existing indexed-format support is retained, including its mode-5 command
  envelope, but has not been validated on that device.
- **Reconnect does not replay commands.** Unsent commands are discarded on
  disconnect, entities become unavailable, and fresh acknowledged state is
  required before new commands are accepted. Already transmitted commands
  cannot be recalled; a multi-command operation can be interrupted.

Keep physical controls and manufacturer safety interlocks in use. Initial
control tests must be attended and explicitly approved by the person responsible
for the fireplace. Do not leave new fireplace automations unattended.

## Troubleshooting and support

- **Cannot connect:** check the host, port, local routing/firewall rules, and
  hostname resolution. A reachable TCP port alone does not confirm the Proflame
  protocol. Setup requires an acknowledgement and valid initial state.
- **Discovery does not appear:** add the integration manually. Discovery is not
  required for operation.
- **Controls revert:** the RF remote or another client may be sending settings.
  Do not repeatedly issue commands to fight another controller.
- **Read-only error:** this is the default behavior, including after upgrading
  entries that lack an explicit mode setting. Use **Reconfigure** to review the
  warning and explicitly enable editing only if appropriate.
- **Sensors are unknown:** some firmware does not report diagnostic fields.
  Missing values are not replaced with invented readings.
- **Temperature is unchanged:** this may be a retained reading, not a connection
  failure or proof that the room temperature is constant.

For a bug report, enable debug logging from the integration's menu, reproduce
the problem safely, then disable logging. Include the integration and HA
versions, fireplace/dongle model and firmware if known, expected behavior, and
relevant logs. **Remove IP addresses, hostnames, MAC addresses, credentials, and
unrelated device information before posting publicly.**

Use this fork's [issue tracker](https://github.com/jimstrang/hass-proflame-connect-wifi/issues),
not the original project's tracker.

## Development and credits

See [CONTRIBUTING.md](CONTRIBUTING.md) for offline tests, protocol references,
staged hardware validation, and HACS release requirements.

Based on work by [Pharrox](https://github.com/Pharrox/hass-proflame-connect-wifi)
and [jeffrapp](https://github.com/jeffrapp/hass-proflame-connect-wifi), with
selective fixes informed by
[jameskorzekwa](https://github.com/jameskorzekwa/hass-proflame-connect-wifi).
Distributed under the [MIT license](LICENSE).
