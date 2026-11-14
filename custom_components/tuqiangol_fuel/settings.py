"""Per-device settings resolution.

Every device can override the account-wide defaults. Settings are stored in the
config entry under ``CONF_DEVICE_SETTINGS`` as::

    device_settings:
      "<imei>":
        scan_interval: 90
        instruction_type_id: "827"
        fuel_param_id: "<param-id>"
        invert: true
        instruction_password: ""

Missing or blank values fall back to the entry-level option and finally to the
constant defaults, so older config entries keep working unchanged.
"""

from __future__ import annotations

from typing import Any, Mapping

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DEVICE_SETTINGS,
    CONF_FUEL_PARAM_ID,
    CONF_INSTRUCTION_PASSWORD,
    CONF_INSTRUCTION_TYPE_ID,
    CONF_INVERT,
    CONF_MC_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_INSTRUCTION_TYPE_ID,
    DEFAULT_INVERT,
    DEFAULT_SCAN_INTERVAL,
)

MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 3600


def clamp_interval(value: Any, fallback: int = DEFAULT_SCAN_INTERVAL) -> int:
    """Return a scan interval inside the supported range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = int(fallback)
    return max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, number))


def device_settings(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    """Return the raw per-device settings mapping."""
    raw = entry.options.get(CONF_DEVICE_SETTINGS)
    if not isinstance(raw, Mapping):
        raw = entry.data.get(CONF_DEVICE_SETTINGS)
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for imei, values in raw.items():
        if isinstance(values, Mapping):
            result[str(imei)] = dict(values)
    return result


def device_setting(entry: ConfigEntry, imei: str) -> dict[str, Any]:
    """Return the settings dict of one device (never None)."""
    return device_settings(entry).get(str(imei), {})


def _entry_default(entry: ConfigEntry, key: str, fallback: Any) -> Any:
    value = entry.options.get(key)
    if value in (None, ""):
        value = entry.data.get(key)
    return fallback if value in (None, "") else value


def scan_interval(entry: ConfigEntry, imei: str) -> int:
    """Effective polling interval for one device, in seconds."""
    override = device_setting(entry, imei).get(CONF_SCAN_INTERVAL)
    if override in (None, ""):
        override = _entry_default(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    return clamp_interval(override)


def instruction_type_id(entry: ConfigEntry, imei: str | None = None) -> str:
    """Effective instruction category id for one device (or the entry default)."""
    if imei is not None:
        override = device_setting(entry, imei).get(CONF_INSTRUCTION_TYPE_ID)
        if override not in (None, ""):
            return str(override)
    return str(
        _entry_default(
            entry, CONF_INSTRUCTION_TYPE_ID, DEFAULT_INSTRUCTION_TYPE_ID
        )
    )


def fuel_param_id(entry: ConfigEntry, imei: str) -> str:
    """Effective fuel parameter id for one device ("" means auto-detect)."""
    override = device_setting(entry, imei).get(CONF_FUEL_PARAM_ID)
    if override not in (None, ""):
        return str(override)
    value = _entry_default(entry, CONF_FUEL_PARAM_ID, "")
    return "" if value in (None, "") else str(value)


def instruction_password(entry: ConfigEntry, imei: str, fallback: str) -> str:
    """Effective instruction password for one device."""
    override = device_setting(entry, imei).get(CONF_INSTRUCTION_PASSWORD)
    if override not in (None, ""):
        return str(override)
    value = _entry_default(entry, CONF_INSTRUCTION_PASSWORD, "")
    return fallback if value in (None, "") else str(value)


def invert(entry: ConfigEntry, imei: str) -> bool:
    """Whether the switch entity state should be inverted for this device."""
    override = device_setting(entry, imei).get(CONF_INVERT)
    if override is None or override == "":
        override = _entry_default(entry, CONF_INVERT, DEFAULT_INVERT)
    if isinstance(override, str):
        return override.strip().lower() in ("1", "true", "yes", "on")
    return bool(override)


def mc_type(entry: ConfigEntry, imei: str) -> str:
    """Device model, preferring per-device settings then the stored maps."""
    override = device_setting(entry, imei).get(CONF_MC_TYPE)
    if override not in (None, ""):
        return str(override)
    return str(
        (entry.options.get(CONF_MC_TYPE) or {}).get(imei)
        or (entry.data.get(CONF_MC_TYPE) or {}).get(imei)
        or ""
    )


def max_supported_interval(entry: ConfigEntry, imeis: list[str]) -> int:
    """Shortest interval any device asks for.

    A single coordinator polls all devices, so it must run at the fastest rate
    requested; slower devices are simply read together with the others.
    """
    if not imeis:
        return clamp_interval(DEFAULT_SCAN_INTERVAL)
    return min(scan_interval(entry, imei) for imei in imeis)
