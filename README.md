# ha-zyxel

<img src="https://raw.githubusercontent.com/zulufoxtrot/ha-zyxel/refs/heads/main/resources/logo.png" alt="Zyxel Logo" width="128"/>


__Home Assistant integration for Zyxel devices__


<img src="https://raw.githubusercontent.com/zulufoxtrot/ha-zyxel/refs/heads/main/resources/screenshot.png" alt="Zyxel Logo" />

[![Open ha-zyxel on Home Assistant Community Store (HACS)](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zulufoxtrot&repository=ha-zyxel&category=integration)

## Supported devices

Confirmed working on:

- LTE7490-M904
- LTE5398-M904
- NR5103v2
- NR5307
- NR7101
- NR7302
- FWA505
- FWA510

Potentially compatible with a lot more devices.
If you do test and find out your device is working, please submit an issue or a pull request and I'll add it to the list.

## Unsupported devices / known issues

- EX3301-TO: https://github.com/zulufoxtrot/ha-zyxel/issues/14
- EX5601-T0: https://github.com/zulufoxtrot/ha-zyxel/issues/11
- EX5601-T1: https://github.com/zulufoxtrot/ha-zyxel/issues/16
- NWA50AX: https://github.com/zulufoxtrot/ha-zyxel/issues/15

## Installation

Prerequisites:

1. The device must be reachable from your home assistant instance (they need to be on the same local network)
2. HTTP access must be enabled in the device's settings (it is the case by default)

### Install via HACS (recommended)

1. Install HACS
2. Navigate to HACS > Menu > Custom Repositories
3. Add this repository: https://github.com/zulufoxtrot/ha-zyxel
4. Set Type to Integration
5. Close the dialog
6. Search for "Zyxel" in HACS
7. Select the Zyxel integration
8. Click Download and confirm
9. Restart HA

### Install manually

1. SSH into your HA instance
2. Navigate to custom_components
3. git clone https://github.com/zulufoxtrot/ha-zyxel
4. Restart your HA instance

## Adding your device

1. Go to HA Settings > Devices & Services.
2. Click Add Integration.
3. Search for Zyxel.
4. Select the Zyxel integration.
5. In Host, type your hostname IP, usually something like 192.168.1.1 (note: enter the full URL scheme with https://)
6. Type your admin username and password
7. Click Submit.

If connection fails, try with http:// instead of https://.

## Adding cards to your dashboard

Add [this code](resources/card_example.yml) to your dashboard to add the cards pictured above. Follow the instructions from the animation below.

Note: the Mushroom card extension is required for the above code to work.

![](resources/import_demo.gif)

## Available entities

In theory, all items listed [here](https://github.com/pkorpine/nr7101?tab=readme-ov-file#example-output) should be available as entities. The entities are generated dynamically, meaning they can vary from one device to another. They depend on what the device lets us see.

## Support

Please submit an [issue](https://github.com/zulufoxtrot/ha-zyxel/issues).

## Credits

This integration uses the [n7101 library](https://github.com/pkorpine/nr7101) by pkorpine.
