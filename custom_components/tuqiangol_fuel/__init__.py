"""The Tuqiangol fuel control integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from . import settings as dev_settings
from .api import (
    TuqiangolAPI,
    TuqiangolAuthError,
    TuqiangolConnectionError,
    normalize_base_url,
)
from .const import CONF_BASE_URL, CONF_DEVICE_IMEI, DEFAULT_BASE_URL, DOMAIN
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


def _async_prune_removed_devices(
    hass: HomeAssistant, entry: ConfigEntry, keep: set[str]
) -> None:
    """Remove entities and devices that are no longer selected.

    Reloading a config entry does not clean the registries: an entity whose
    device was deselected simply stops being created and lingers forever as
    ``unavailable``. Remove them so the entry only lists selected devices.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    wanted = {f"{DOMAIN}_{imei}_fuel" for imei in keep}
    for item in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        if item.platform != DOMAIN or item.unique_id in wanted:
            continue
        _LOGGER.debug("移除已取消选择的实体 %s", item.entity_id)
        ent_reg.async_remove(item.entity_id)

    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        imeis = {
            identifier[1]
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
        }
        if imeis and not (imeis & keep):
            _LOGGER.debug("移除已取消选择的设备 %s", device.name or device.id)
            dev_reg.async_remove_device(device.id)


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

    supported: list[str] = []
    for imei in async_selected_imeis(entry):
        if imei not in device_map:
            _LOGGER.warning("设备 %s 不在账号设备列表中，已跳过", imei)
            continue
        mc_type = dev_settings.mc_type(entry, imei) or device_map[imei].get(
            "mc_type", ""
        )
        type_id = dev_settings.instruction_type_id(entry, imei)
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

    # Remove registry entries of devices the user deselected. Base this on the
    # selection, not on `supported`: a temporary platform failure must never
    # delete entities for devices the user still wants.
    _async_prune_removed_devices(hass, entry, set(async_selected_imeis(entry)))

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
