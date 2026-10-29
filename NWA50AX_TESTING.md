# NWA50AX development checkpoint

Status: **incomplete experimental implementation; not yet validated on hardware**.

Implemented:
- Separate NWA50AX option, preserving the router default for existing entries.
- Local login form parsing and CSRF submission; isolated cookie session per AP.
- Password copied to both `pwd` and `password`, matching the inspected device form.
- A single retry after session expiry; bounded HTTP requests and serialized polling.
- Safe literal parsing of `zyshdata`, never execution of returned JavaScript.
- Model/firmware sensors and parsing of radio counters/channel/power/utilization.
- No reboot button for AP entries. No write commands.
- HTTPS certificate validation remains enabled; no automatic HTTPS downgrade.

## Remaining before a full sensor release

The polling commands were verified against the dashboard Network payload on
2026-10-21: `show version`, `show wlan all`, `show wireless-hal current channel`,
`show wireless-hal statistic`, `show fqdn`, and `show nebula cloud status`.
They request device information and radio channel/counters/power/utilization using
`filter=js2` and `write=0`. Radio parsers use anonymized captured response examples.

Real-device automatic login and Home Assistant setup remain untested. The browser
session was inspected, but no credentials were exported and no automatic API login
was attempted. Test one device first with its existing local management URL and
Nebula-assigned admin password entered in Home Assistant (not in GitHub or chat).

## Checks

`python -m unittest discover -s tests -v`
`python -m compileall -q custom_components`

Tests cover login form/token parsing, submitted form fields, malformed responses,
executable-code rejection, unsupported models, radio slot mapping, failed command
omission, bad credentials and bounded session retries. They use mocked HTTP
responses, not a live AP or a running Home Assistant.

## Installation after the remaining work is complete

Back up the existing `custom_components/ha_zyxel` directory. This fork uses the same
integration domain as upstream, so replace the whole integration, never individual
files or two copies side by side. Restart Home Assistant after installation. Select
NWA50AX (experimental) in the new config flow. No production installation has been
performed as part of this checkpoint.
