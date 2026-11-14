"""The Tuqiangol fuel control integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    TuqiangolAPI,
    TuqiangolAuthError,
    TuqiangolConnectionError,
    normalize_base_url,
)
from .const import (
    CONF_BASE_URL,
    CONF_DEVICE_IMEI,
    CONF_INSTRUCTION_TYPE_ID,
    CONF_MC_TYPE,
    DEFAULT_BASE_URL,
    DEFAULT_INSTRUCTION_TYPE_ID,
    DOMAIN,
)
from .coordinator import TuqiangolCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH]


@dataclass
class TuqiangolRuntime:
    """Runtime objects kept for one config entry."""

    api: TuqiangolAPI
    coordinator: TuqiangolCoordinator
    devices: dict[str, dict[str, str]]


def async_selected_imeis(entry: ConfigEntry) -> list[str]:
    """Return selected IMEIs from options, falling back to the entry data."""
    return list(
        entry.options.get(CONF_DEVICE_IMEI)
        or entry.data.get(CONF_DEVICE_IMEI)
        or []
    )


def async_mc_type(entry: ConfigEntry, imei: str) -> str:
    """Return the device model, preferring the value stored in options."""
    return (
        (entry.options.get(CONF_MC_TYPE) or {}).get(imei)
        or (entry.data.get(CONF_MC_TYPE) or {}).get(imei)
        or ""
    )


def async_instruction_type_id(entry: ConfigEntry) -> str:
    """Return the instruction category id."""
    return str(
        entry.options.get(CONF_INSTRUCTION_TYPE_ID)
        or entry.data.get(CONF_INSTRUCTION_TYPE_ID)
        or DEFAULT_INSTRUCTION_TYPE_ID
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuqiangol fuel control from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # A dedicated session per config entry: the login state is a cookie, so
    # sharing Home Assistant's global session would mix up multiple accounts.
    session = async_create_clientsession(hass)

    api = TuqiangolAPI(
        session,
        normalize_base_url(entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    try:
        await api.login()
        devices = await api.async_get_devices()
    except TuqiangolAuthError as err:
        # Wrong credentials must not be retried forever: ask the user to
        # re-authenticate instead.
        raise ConfigEntryAuthFailed(str(err)) from err
    except TuqiangolConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    except Exception as err:  # noqa: BLE001 - transient failures retry later
        raise ConfigEntryNotReady(str(err)) from err

    device_map = {item["imei"]: item for item in devices}
    type_id = async_instruction_type_id(entry)

    supported: list[str] = []
    for imei in async_selected_imeis(entry):
        if imei not in device_map:
            _LOGGER.warning("设备 %s 不在账号设备列表中，已跳过", imei)
            continue
        mc_type = async_mc_type(entry, imei) or device_map[imei].get("mc_type", "")
        try:
            await api.async_get_fuel_instruction(imei, mc_type, type_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "设备 %s (%s) 不支持断油路指令，已跳过: %s",
                device_map[imei].get("name"),
                imei,
                err,
            )
            continue
        supported.append(imei)

    if not supported:
        _LOGGER.warning("集成 %s 没有可用的断油路设备，请在“配置”中重新选择", entry.title)

    coordinator = TuqiangolCoordinator(hass, entry, api, supported, device_map)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = TuqiangolRuntime(api, coordinator, device_map)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
