"""Switch platform: cut / restore the fuel circuit."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import settings as dev_settings
from .const import (
    CODE_OFFLINE_ERROR,
    CODE_OFFLINE_SAVED,
    CODE_OK,
    CONF_BASE_URL,
    DOMAIN,
    MANUFACTURER,
    MODEL,
    STATE_CUT,
    STATE_RESTORE,
)
from .coordinator import TuqiangolCoordinator

_LOGGER = logging.getLogger(__name__)

ACTION_CUT = "cut"
ACTION_RESTORE = "restore"

CODE_LABELS = {
    CODE_OK: "指令已执行",
    CODE_OFFLINE_SAVED: "终端离线，指令已入队",
    CODE_OFFLINE_ERROR: "终端离线，入队失败",
}


def _format_message(result: dict[str, Any]) -> str:
    """Turn the platform reply into a short human readable string."""
    data = result.get("data")
    if isinstance(data, dict):
        detail = data.get("msg") or (data.get("log") or {}).get("content") or ""
        return str(detail or result.get("msg") or "")
    if data:
        return str(data)
    return str(result.get("msg") or "")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one fuel switch per selected device."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TuqiangolFuelSwitch(
            runtime.coordinator, entry, imei, runtime.devices.get(imei, {})
        )
        for imei in runtime.coordinator.imeis
    )


class TuqiangolFuelSwitch(CoordinatorEntity[TuqiangolCoordinator], SwitchEntity):
    """Switch entity.

    Normally ``on`` means the fuel circuit is cut. When ``invert`` is enabled
    for the device the displayed state is flipped, so ``on`` means the circuit
    is restored. The command sent always matches what the user asked for: the
    entity maps its own ``on``/``off`` to the correct platform action.
    """

    _attr_has_entity_name = True

    # Fallback used while the platform state is still unknown.
    _attr_icon = "mdi:valve"

    def __init__(
        self,
        coordinator: TuqiangolCoordinator,
        entry: ConfigEntry,
        imei: str,
        device: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._imei = imei
        self._attr_unique_id = f"{DOMAIN}_{imei}_fuel"
        self._attr_translation_key = "fuel"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, imei)},
            name=device.get("name") or imei,
            manufacturer=MANUFACTURER,
            model=device.get("mc_type") or MODEL,
            configuration_url=entry.data.get(CONF_BASE_URL),
        )
        self._attr_extra_state_attributes: dict[str, Any] = {
            "imei": imei,
            "last_action": None,
            "last_code": None,
            "last_result": None,
            "last_message": None,
            "last_record_time": None,
        }

    @property
    def icon(self) -> str:
        """Reflect the physical fuel circuit state.

        The icon follows the real state of the circuit, so an inverted switch
        still shows what the vehicle is actually doing.
        """
        payload = (self.coordinator.data or {}).get(self._imei) or {}
        state = payload.get("state")
        if state == STATE_CUT:
            return "mdi:valve-closed"
        if state == STATE_RESTORE:
            return "mdi:valve-open"
        return self._attr_icon

    @property
    def _inverted(self) -> bool:
        """Whether this device's displayed polarity is flipped."""
        return dev_settings.invert(self._entry, self._imei)

    @property
    def is_on(self) -> bool | None:
        """Displayed state, honouring the per-device ``invert`` setting."""
        payload = (self.coordinator.data or {}).get(self._imei) or {}
        state = payload.get("state")
        if state is None:
            return None
        cut = state == STATE_CUT
        return (not cut) if self._inverted else cut

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the raw platform state and the effective settings."""
        attrs = dict(self._attr_extra_state_attributes)
        payload = (self.coordinator.data or {}).get(self._imei) or {}
        state = payload.get("state")
        attrs["fuel_circuit"] = {
            STATE_CUT: "断油路",
            STATE_RESTORE: "恢复油路",
        }.get(state)
        attrs["inverted"] = self._inverted
        attrs["scan_interval"] = dev_settings.scan_interval(self._entry, self._imei)
        attrs["instruction_type_id"] = dev_settings.instruction_type_id(
            self._entry, self._imei
        )
        attrs["fuel_param_id"] = dev_settings.fuel_param_id(self._entry, self._imei) or "auto"
        return attrs

    @property
    def available(self) -> bool:
        """Available while this specific device can be read.

        Uses a per-device flag: one broken device must not hide the state of
        the others, and a stale value must not be presented as fresh.
        """
        return (
            self.coordinator.last_update_success
            and self._imei in (self.coordinator.data or {})
            and self.coordinator.async_is_available(self._imei)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the entity to ``on`` (cut, or restore when inverted)."""
        await self._async_send(ACTION_RESTORE if self._inverted else ACTION_CUT)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the entity to ``off`` (restore, or cut when inverted)."""
        await self._async_send(ACTION_CUT if self._inverted else ACTION_RESTORE)

    def _set_result(self, action: str, result: dict[str, Any]) -> None:
        code = result.get("code")
        label = CODE_LABELS.get(code, "未知")
        if self.coordinator.already_in_state(action, result):
            label = "终端已处于目标状态，未重复执行"
        elif self.coordinator.contradicts(action, result):
            label = "未执行：终端处于相反状态"
        self._attr_extra_state_attributes.update(
            {
                "last_action": action,
                "last_code": code,
                "last_result": label,
                "last_message": _format_message(result),
            }
        )

    async def _async_send(self, action: str) -> None:
        try:
            result = await self.coordinator.async_send_fuel(action, self._imei)
        except Exception as err:  # noqa: BLE001 - reported to the UI
            _LOGGER.error("设备 %s 油路指令下发异常: %s", self._imei, err)
            self._attr_extra_state_attributes.update(
                {
                    "last_action": action,
                    "last_code": None,
                    "last_result": "异常",
                    "last_message": str(err),
                }
            )
            # a failed send must not leave a wrong optimistic state behind
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
            return

        self._set_result(action, result)
        code = result.get("code")

        if code == CODE_OK:
            _LOGGER.info("设备 %s 油路指令已执行: %s", self._imei, result.get("data"))
        elif code == CODE_OFFLINE_SAVED:
            _LOGGER.warning("设备 %s 当前离线，指令已排队，终端上线后执行", self._imei)
        elif code == CODE_OFFLINE_ERROR:
            _LOGGER.error("设备 %s 离线入队失败，指令未生效", self._imei)
        else:
            _LOGGER.warning("设备 %s 指令返回 %s", self._imei, code)

        record = (self.coordinator.data or {}).get(self._imei, {}).get("record") or {}
        if record:
            self._attr_extra_state_attributes["last_record_time"] = record.get(
                "updateTime"
            )
        self.async_write_ha_state()

        # Confirm with the platform, but never let a stale record undo the
        # state the command has just produced.
        await self.coordinator.async_request_refresh()
        record = (self.coordinator.data or {}).get(self._imei, {}).get("record") or {}
        if record:
            self._attr_extra_state_attributes["last_record_time"] = record.get(
                "updateTime"
            )
        self.async_write_ha_state()
