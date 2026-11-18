> 🤖 **This release was performed entirely by an agent.**

## 📢 This project is looking for maintainers

If you want to keep `ha-zyxel` alive, **tag @zulufoxtrot**. There is no active maintainer and no device available for proper testing.

---

## What changed since v0.2.8

### Upgrading from v0.2.8: read this first
`v0.2.8` was tagged before its code was merged, so the release shipped a manifest that still declared version `0.2.7`. Home Assistant and HACS therefore showed `0.2.8` installed while the integration reported `0.2.7` (#78). **This release ships a manifest that matches its tag**, and CI now fails if a tag and the manifest disagree. You can upgrade straight to `v0.2.10`.

Two stray tags (`v1.0.5`, `v1.0.6`) were left over from July 2025 and pointed at commits that declare version `0.1.5`; they were removed so the HACS version picker cannot offer them.

### Connection reliability
- **One keep-alive connection instead of a TLS handshake per request**, with a proper logout on unload (#76, thanks @host-felice). This is the change behind the "modem stops answering / Home Assistant stops responding" reports (#32, #52, #64).
- **Every request is bounded.** A request the modem never answered used to hold a worker thread forever; polls now have a budget, a busy router fails instead of queueing, and a timeout ends the poll instead of piling more requests onto a struggling modem.
- Serialized access to the modem so a slow poll can no longer invalidate the AES key of one still in flight, which caused the random `'utf-8' codec can't decode byte ...` errors.

### Device information
- The device is named after its model (e.g. `Zyxel EX5601-T1`) without a duplicated `Zyxel` prefix (#70).
- Manufacturer, hardware version and serial number are read from the modem when it reports them (#79, thanks @GrumpyMeow).

### Entities
- **Readable, unique sensor names** that include the section and interface, e.g. `Traffic br0 Bytes Received` (#67).
- Sensors that repeat the `Device` values under `Overview` are no longer created twice (#68).
- Most of the hundreds of generic, mostly-static sensors are now **created disabled by default**; enable the ones you want from the entity registry (#68). This addresses the 572-entity report (#54).
- **New switches** for port-forward rules and Wi-Fi networks, each enabled/disabled from Home Assistant (#69).
- The reboot button works again on newer firmware, which rejected the old URL-style session key (#69).

### Compatibility
- Removed the deprecated `CONNECTION_CLASS` from the config flow (#75, thanks @kolalind).
- The `nr7101` library is now `v2.0.3`, which accepts an existing `requests.Session`.

### Tests
- A regression suite now runs on every push and pull request: sensor naming and filtering, device info, request encryption and timeouts, and a fake modem that goes mute, slow, trickles, stalls and drops connections.

**Full changelog:** https://github.com/zulufoxtrot/ha-zyxel/compare/v0.2.8...v0.2.10
