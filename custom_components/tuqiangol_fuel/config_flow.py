"""Config flow for Tuqiangol fuel control."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    TuqiangolAPI,
    TuqiangolAuthError,
    TuqiangolConnectionError,
    TuqiangolError,
    normalize_base_url,
)
from .const import (
    CONF_BASE_URL,
    CONF_DEVICE_IMEI,
    CONF_FUEL_PARAM_ID,
    CONF_INSTRUCTION_PASSWORD,
    CONF_INSTRUCTION_TYPE_ID,
    CONF_MC_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_INSTRUCTION_TYPE_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _async_fetch_devices(
    hass, base_url: str, username: str, password: str, type_id: str
) -> list[dict[str, Any]]:
    """Log in and return only devices that really expose the fuel instruction.

    A private session is used on purpose: the login state is a cookie, so two
    flows running at the same time must not share Home Assistant's session.
    """
    session = async_create_clientsession(hass, auto_cleanup=False)
    try:
        api = TuqiangolAPI(session, base_url, username, password)
        await api.login()
        devices = await api.async_get_devices()

        supported: list[dict[str, Any]] = []
        for device in devices:
            try:
                await api.async_get_fuel_instruction(
                    device["imei"], device.get("mc_type", ""), type_id
                )
            except TuqiangolError:
                continue
            supported.append(device)
        return supported
    finally:
        session.detach()


class TuqiangolFuelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow state (never use class attributes)."""
        self._devices: list[dict[str, Any]] = []
        self._account: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for account credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            try:
                devices = await _async_fetch_devices(
                    self.hass,
                    base_url,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    DEFAULT_INSTRUCTION_TYPE_ID,
                )
            except TuqiangolAuthError:
                errors["base"] = "invalid_auth"
            except TuqiangolConnectionError:
                errors["base"] = "cannot_connect"
            except TuqiangolError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001 - never leak a traceback to the UI
                _LOGGER.exception("连接途强在线时发生未预期错误")
                errors["base"] = "unknown"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    # normalise first, so "www.x.com", "https://www.x.com" and
                    # "https://www.x.com/" are all recognised as the same account
                    await self.async_set_unique_id(
                        f"{user_input[CONF_USERNAME]}@{normalize_base_url(base_url)}"
                    )
                    self._abort_if_unique_id_configured()
                    self._devices = devices
                    self._account = user_input
                    return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a re-authentication request after the credentials broke."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and verify it against the platform."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            username = entry.data[CONF_USERNAME]
            try:
                devices = await _async_fetch_devices(
                    self.hass,
                    base_url,
                    username,
                    password,
                    str(
                        entry.options.get(CONF_INSTRUCTION_TYPE_ID)
                        or entry.data.get(
                            CONF_INSTRUCTION_TYPE_ID, DEFAULT_INSTRUCTION_TYPE_ID
                        )
                    ),
                )
            except TuqiangolAuthError:
                errors["base"] = "invalid_auth"
            except TuqiangolConnectionError:
                errors["base"] = "cannot_connect"
            except TuqiangolError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("重新认证时发生未预期错误")
                errors["base"] = "unknown"
            else:
                new_data = dict(entry.data)
                new_data[CONF_PASSWORD] = password
                new_options = dict(entry.options)
                if not new_options.get(CONF_INSTRUCTION_PASSWORD):
                    new_options[CONF_INSTRUCTION_PASSWORD] = password
                self.hass.config_entries.async_update_entry(
                    entry, data=new_data, options=new_options
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the devices that should expose a fuel switch."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = [
                imei
                for imei in user_input.get(CONF_DEVICE_IMEI, []) or []
                if any(d["imei"] == imei for d in self._devices)
            ]
            if not selected:
                errors["base"] = "no_devices"
            else:
                data = dict(self._account)
                data[CONF_DEVICE_IMEI] = selected
                data[CONF_MC_TYPE] = {
                    imei: next(
                        (d["mc_type"] for d in self._devices if d["imei"] == imei), ""
                    )
                    for imei in selected
                }
                data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
                data[CONF_INSTRUCTION_TYPE_ID] = DEFAULT_INSTRUCTION_TYPE_ID
                data[CONF_INSTRUCTION_PASSWORD] = data[CONF_PASSWORD]
                return self.async_create_entry(
                    title=f"途强油路控制 ({data[CONF_USERNAME]})", data=data
                )

        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_IMEI): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": d["imei"],
                                    "label": f"{d['name']} ({d['imei']})",
                                }
                                for d in self._devices
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TuqiangolFuelOptionsFlow()


class TuqiangolFuelOptionsFlow(OptionsFlow):
    """Select devices and tune polling / instruction parameters."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}

        type_id = str(
            entry.options.get(CONF_INSTRUCTION_TYPE_ID)
            or entry.data.get(CONF_INSTRUCTION_TYPE_ID)
            or DEFAULT_INSTRUCTION_TYPE_ID
        )

        try:
            devices = await _async_fetch_devices(
                self.hass,
                entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
                type_id,
            )
        except TuqiangolAuthError:
            errors["base"] = "invalid_auth"
            devices = []
        except TuqiangolConnectionError:
            errors["base"] = "cannot_connect"
            devices = []
        except TuqiangolError:
            errors["base"] = "unknown"
            devices = []
        except Exception:  # noqa: BLE001
            _LOGGER.exception("读取设备列表时发生未预期错误")
            errors["base"] = "unknown"
            devices = []

        current = entry.options.get(
            CONF_DEVICE_IMEI, entry.data.get(CONF_DEVICE_IMEI, [])
        )

        if user_input is not None:
            selected = [
                imei
                for imei in user_input.get(CONF_DEVICE_IMEI, []) or []
                if any(d["imei"] == imei for d in devices)
            ]
            if not selected and devices:
                errors["base"] = "no_devices"
            else:
                mc_types = {
                    imei: next(
                        (d["mc_type"] for d in devices if d["imei"] == imei), ""
                    )
                    for imei in selected
                } or entry.data.get(CONF_MC_TYPE, {})
                new_options = dict(entry.options)
                new_options.update(
                    {
                        CONF_DEVICE_IMEI: selected,
                        CONF_MC_TYPE: mc_types,
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                        CONF_INSTRUCTION_TYPE_ID: user_input.get(
                            CONF_INSTRUCTION_TYPE_ID, DEFAULT_INSTRUCTION_TYPE_ID
                        ),
                        CONF_INSTRUCTION_PASSWORD: user_input.get(
                            CONF_INSTRUCTION_PASSWORD, entry.data[CONF_PASSWORD]
                        ),
                        CONF_FUEL_PARAM_ID: user_input.get(CONF_FUEL_PARAM_ID, ""),
                    }
                )
                return self.async_create_entry(title="", data=new_options)

        # Without a device list a SelectSelector cannot render the saved values,
        # so tell the user instead of showing a broken form.
        if not devices:
            return self.async_abort(reason="no_devices")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_IMEI, default=current): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {
                                    "value": d["imei"],
                                    "label": f"{d['name']} ({d['imei']})",
                                }
                                for d in devices
                            ],
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=entry.options.get(
                            CONF_SCAN_INTERVAL,
                            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                        ),
                    ): vol.All(int, vol.Range(min=15, max=3600)),
                    vol.Optional(
                        CONF_INSTRUCTION_TYPE_ID,
                        default=type_id,
                    ): str,
                    vol.Optional(
                        CONF_INSTRUCTION_PASSWORD,
                        default=entry.options.get(
                            CONF_INSTRUCTION_PASSWORD,
                            entry.data.get(
                                CONF_INSTRUCTION_PASSWORD, entry.data[CONF_PASSWORD]
                            ),
                        ),
                    ): str,
                    vol.Optional(
                        CONF_FUEL_PARAM_ID,
                        default=entry.options.get(CONF_FUEL_PARAM_ID, "")
                        or entry.data.get(CONF_FUEL_PARAM_ID, ""),
                    ): str,
                }
            ),
            errors=errors,
        )
