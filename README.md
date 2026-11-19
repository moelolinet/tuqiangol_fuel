<div align="center">

<img src="custom_components/tuqiangol_fuel/brand/icon@2x.png" width="150" alt="Tuqiangol Fuel">

# Tuqiangol Fuel

Home Assistant integration for the fuel relay of Tuqiangol GPS trackers

[![简体中文](https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-0969da?style=for-the-badge&logo=readme&logoColor=white)](README.zh-CN.md)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub release](https://img.shields.io/github/v/release/moelolinet/tuqiangol_fuel)](https://github.com/moelolinet/tuqiangol_fuel/releases)

</div>

---

Control the external relay fuel circuit of Tuqiangol (`tuqiangol.com`) GPS trackers
directly from Home Assistant.

One feature only: **cut the fuel circuit / restore it**.

---

## Features

- Per-device parameters that never affect each other
- Switch icon follows the actual fuel circuit state
- Optional state inversion, per device
- Dedicated domain, so it does not clash with other integrations
- No third-party dependencies

---

## Entities

Each enabled device creates one switch.

| State | Default meaning |
| --- | --- |
| `on` | Fuel circuit cut |
| `off` | Fuel circuit restored |

With *invert* enabled the meaning is reversed.

---

## Installation

**HACS**

1. HACS → ⋮ → Custom repositories
2. Add the URL of this repository with category **Integration**
3. Install and restart Home Assistant

**Manual**

Copy `custom_components/tuqiangol_fuel` into your Home Assistant configuration
directory, restart, then force-refresh the browser.

---

## Configuration

After adding the integration:

1. Enter the site URL and the account credentials
2. Select the devices to expose
3. Confirm the parameters for each device

### Device parameters

| Parameter | Meaning |
| --- | --- |
| State refresh interval | 15–3600 seconds; leave empty for the default |
| Instruction category ID | Leave empty for the default |
| Fuel parameter ID | Leave empty to detect it automatically |
| Instruction password | Leave empty to use the login password |
| Invert switch state | When enabled, `on` = fuel circuit restored |

Every parameter can be changed later per device through the integration's **Configure** dialog.

---

## Usage

```yaml
service: switch.turn_on
target:
  entity_id: switch.<DEVICE>_fuel_circuit
```

`turn_off` performs the opposite action.

---

## Notes

- Cutting the fuel circuit requires the tracker to report a valid position and a low
  vehicle speed; the platform makes that decision.
- When the tracker is offline the command is queued and executed once it comes back online.
- Account and device parameters are stored locally in the Home Assistant configuration.
- This project is not affiliated with Tuqiangol.
- The icon requires Home Assistant 2026.3 or newer; force-refresh the browser cache
  if the old icon is still shown after upgrading.

---

## License

GPL-3.0
