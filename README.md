# ha-zyxel

<img src="https://raw.githubusercontent.com/zulufoxtrot/ha-zyxel/refs/heads/main/resources/logo.png" alt="Zyxel Logo" width="128"/>

> 📢 🤓 **This project is looking for maintainers** 📢 🤓
> 
> If you are interested, get in touch!

__Home Assistant integration for Zyxel devices__

<img src="https://raw.githubusercontent.com/zulufoxtrot/ha-zyxel/refs/heads/main/resources/screenshot.png" alt="Zyxel Logo" />

[![Open ha-zyxel on Home Assistant Community Store (HACS)](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zulufoxtrot&repository=ha-zyxel&category=integration)

## Supported devices

Confirmed working on:

- AX7501-B0
- FWA505
- FWA510
- FWA710 5G V2
- LTE3202-M437
- LTE7490-M904
- LTE5398-M904
- NR5103E
- NR5103v2
- NR5307
- NR7101
- NR7102
- NR7302
- VMG3625-T50B
- VMG4005-B50A
- VMG8825-T50

Potentially compatible with a lot more devices.
If you do test and find out your device is working, please submit an issue or a pull request and I'll add it to the list.

## Installation

Prerequisites:

1. The device must be reachable from your home assistant instance (they need to be on the same local network)
2. HTTP access must be enabled in the device's settings (it is the case by default)

### Install via HACS (recommended)

1. Install HACS
2. Click the big blue button above
3. Click Download and confirm
4. Restart HA

### Install manually

1. SSH into your HA instance
3. `git clone https://github.com/zulufoxtrot/ha-zyxel`
2. Navigate to `ha-zyxel/custom_components`
4. Copy `ha_zyxel` to your HA instance's `custom_components` directory
4. Restart your HA instance

## Adding a device

1. Go to HA Settings > Devices & Services.
2. Click Add Integration.
3. Search for Zyxel.
4. Select the Zyxel integration.
5. In Host, type your hostname IP, usually something like https://192.168.1.1 (⚠️ enter the full URL scheme with `https://`)
6. Type your admin username and password
7. Click Submit.

If connection fails, try with `http://` instead of `https://`.

## Adding cards to your dashboard

Add [this code](resources/card_example.yml) to your dashboard to add the cards pictured above. Follow the instructions from the animation below.

Note: the Mushroom card extension is required for the above code to work.

![](resources/import_demo.gif)

## Available entities

By default, the integration exposes one canonical entity for each useful router
value instead of registering every raw field returned by the device. This avoids
hundreds of duplicate entities from repeated payload sections.

The curated set includes:

- Cellular signal and quality measurements
- LTE and 5G access technology, bands, and carrier aggregation
- Mobile network, cell, site, and tracking-area information
- Router uptime and temperature measurements
- Cellular connection, SIM-ready, and roaming states
- Router traffic counters
- A connected-device count sensor

Device trackers, client diagnostics, and uncurated raw router telemetry are
opt-in. Enabling raw telemetry may create hundreds of disabled entities because
the router returns duplicate and device-specific fields.

When upgrading from a version that exposed every field, stale generated entities
are removed from the entity registry. Recorder history remains associated with
the old entity IDs until Home Assistant purges it normally.

## Options

Select **Configure** on the integration to change:

- The router scan interval
- How long an unseen client is considered home
- Whether to create trackers for discovered clients
- Whether to expose per-client connectivity and diagnostic entities
- Whether to expose all uncurated router fields

## Support

Please submit an [issue](https://github.com/zulufoxtrot/ha-zyxel/issues).

## Credits

This integration uses the [n7101 library](https://github.com/pkorpine/nr7101) by pkorpine.
