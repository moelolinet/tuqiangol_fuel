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
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import settings as dev_settings
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
    CONF_DEVICE_SETTINGS,
    CONF_FUEL_PARAM_ID,
    CONF_INSTRUCTION_PASSWORD,
    CONF_INSTRUCTION_TYPE_ID,
    CONF_INVERT,
    CONF_MC_TYPE,
    CONF_SCAN_INTERVAL,
    DEFAULT_BASE_URL,
    DEFAULT_INSTRUCTION_TYPE_ID,
    DEFAULT_INVERT,
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


def _device_form_schema(
    defaults: dict[str, Any],
    name: str,
    key_prefix: str,
    allow_blank_password: bool,
) -> vol.Schema:
    """Build the per-device settings form.

    ``name`` is only used in labels so the user knows which device is edited.
    The ``key_prefix`` keeps field names unique between devices.
    """
    schema: dict[Any, Any] = {
        vol.Optional(
            f"{key_prefix}{CONF_SCAN_INTERVAL}",
            default=defaults.get(CONF_SCAN_INTERVAL, ""),
        ): str,
        vol.Optional(
            f"{key_prefix}{CONF_INSTRUCTION_TYPE_ID}",
            default=defaults.get(CONF_INSTRUCTION_TYPE_ID, ""),
        ): str,
        vol.Optional(
            f"{key_prefix}{CONF_FUEL_PARAM_ID}",
            default=defaults.get(CONF_FUEL_PARAM_ID, ""),
        ): str,
        vol.Optional(
            f"{key_prefix}{CONF_INSTRUCTION_PASSWORD}",
            default=defaults.get(CONF_INSTRUCTION_PASSWORD, ""),
        ): str,
        vol.Optional(
            f"{key_prefix}{CONF_INVERT}",
            default=bool(defaults.get(CONF_INVERT, DEFAULT_INVERT)),
        ): bool,
    }
    return vol.Schema(schema)


def _collect_device_settings(
    user_input: dict[str, Any], prefix: str, imei: str
) -> dict[str, Any]:
    """Extract one device's values from flat form keys, dropping blanks."""
    result: dict[str, Any] = {}
    interval = str(user_input.get(f"{prefix}{CONF_SCAN_INTERVAL}") or "").strip()
    if interval:
        try:
            result[CONF_SCAN_INTERVAL] = dev_settings.clamp_interval(int(interval))
        except (TypeError, ValueError):
            pass
    for key in (
        CONF_INSTRUCTION_TYPE_ID,
        CONF_FUEL_PARAM_ID,
        CONF_INSTRUCTION_PASSWORD,
    ):
        value = str(user_input.get(f"{prefix}{key}") or "").strip()
        if value:
            result[key] = value
    if CONF_INVERT:
        result[CONF_INVERT] = bool(user_input.get(f"{prefix}{CONF_INVERT}", False))
    return result

class TuqiangolFuelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup: credentials -> devices -> per-device settings."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow state (never use class attributes)."""
        self._devices: list[dict[str, Any]] = []
        self._account: dict[str, Any] = {}
        self._pending: list[str] = []
        self._settings: dict[str, dict[str, Any]] = {}

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
                    dev_settings.instruction_type_id(entry),
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
                # only follow the new login password where nothing was set
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
                self._pending = list(selected)
                self._settings = {}
                return await self.async_step_device()

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

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure one device at a time, then finish."""
        if not self._pending:
            return self._async_finish()

        imei = self._pending[0]
        device = next((d for d in self._devices if d["imei"] == imei), {})

        if user_input is not None:
            self._settings[imei] = _collect_device_settings(user_input, "", imei)
            self._pending.pop(0)
            if not self._pending:
                return self._async_finish()
            return await self.async_step_device()

        defaults = {
            CONF_SCAN_INTERVAL: str(DEFAULT_SCAN_INTERVAL),
            CONF_INSTRUCTION_TYPE_ID: DEFAULT_INSTRUCTION_TYPE_ID,
            CONF_FUEL_PARAM_ID: "",
            CONF_INSTRUCTION_PASSWORD: "",
            CONF_INVERT: DEFAULT_INVERT,
        }
        return self.async_show_form(
            step_id="device",
            description_placeholders={
                "name": str(device.get("name") or imei),
                "imei": imei,
                "remaining": str(len(self._pending)),
            },
            data_schema=_device_form_schema(defaults, imei, "", True),
            errors={},
        )

    def _async_finish(self) -> ConfigFlowResult:
        """Create the entry with the collected per-device settings."""
        data = dict(self._account)
        data[CONF_DEVICE_IMEI] = list(self._settings)
        data[CONF_MC_TYPE] = {
            imei: next((d["mc_type"] for d in self._devices if d["imei"] == imei), "")
            for imei in self._settings
        }
        data[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        data[CONF_INSTRUCTION_TYPE_ID] = DEFAULT_INSTRUCTION_TYPE_ID
        data[CONF_INSTRUCTION_PASSWORD] = data[CONF_PASSWORD]
        data[CONF_DEVICE_SETTINGS] = dict(self._settings)
        return self.async_create_entry(
            title=f"途强油路控制 ({data[CONF_USERNAME]})", data=data
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return TuqiangolFuelOptionsFlow()


class TuqiangolFuelOptionsFlow(OptionsFlow):
    """Pick devices, then tune every device individually."""

    def __init__(self) -> None:
        """Initialise per-flow state."""
        self._devices: list[dict[str, Any]] = []
        self._selected: list[str] = []
        self._pending: list[str] = []
        self._settings: dict[str, dict[str, Any]] = {}
        self._type_id = DEFAULT_INSTRUCTION_TYPE_ID

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select which devices are enabled."""
        entry = self.config_entry
        errors: dict[str, str] = {}

        if not self._devices:
            self._type_id = dev_settings.instruction_type_id(entry)
            try:
                self._devices = await _async_fetch_devices(
                    self.hass,
                    entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
                    entry.data[CONF_USERNAME],
                    entry.data[CONF_PASSWORD],
                    self._type_id,
                )
            except TuqiangolAuthError:
                errors["base"] = "invalid_auth"
            except TuqiangolConnectionError:
                errors["base"] = "cannot_connect"
            except TuqiangolError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("读取设备列表时发生未预期错误")
                errors["base"] = "unknown"

        if not self._devices and not errors:
            return self.async_abort(reason="no_devices")

        current = entry.options.get(
            CONF_DEVICE_IMEI, entry.data.get(CONF_DEVICE_IMEI, [])
        )

        if user_input is not None:
            selected = [
                imei
                for imei in user_input.get(CONF_DEVICE_IMEI, []) or []
                if any(d["imei"] == imei for d in self._devices)
            ]
            if not selected:
                errors["base"] = "no_devices"
            else:
                self._selected = selected
                self._pending = list(selected)
                # keep settings of devices that stay enabled
                existing = dev_settings.device_settings(entry)
                self._settings = {
                    imei: dict(existing.get(imei, {})) for imei in selected
                }
                return await self.async_step_device()

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

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one device, then move to the next."""
        entry = self.config_entry

        if not self._pending:
            return self._async_finish()

        imei = self._pending[0]
        device = next((d for d in self._devices if d["imei"] == imei), {})

        if user_input is not None:
            current = dict(self._settings.get(imei, {}))
            collected = _collect_device_settings(user_input, "", imei)
            current.update(collected)
            self._settings[imei] = current
            self._pending.pop(0)
            if not self._pending:
                return self._async_finish()
            return await self.async_step_device()

        current = self._settings.get(imei, {})
        defaults = {
            CONF_SCAN_INTERVAL: str(
                current.get(CONF_SCAN_INTERVAL)
                or dev_settings.scan_interval(entry, imei)
            ),
            CONF_INSTRUCTION_TYPE_ID: str(
                current.get(CONF_INSTRUCTION_TYPE_ID)
                or dev_settings.instruction_type_id(entry, imei)
            ),
            CONF_FUEL_PARAM_ID: str(
                current.get(CONF_FUEL_PARAM_ID)
                or dev_settings.fuel_param_id(entry, imei)
            ),
            CONF_INSTRUCTION_PASSWORD: str(
                current.get(CONF_INSTRUCTION_PASSWORD) or ""
            ),
            CONF_INVERT: dev_settings.invert(entry, imei),
        }
        return self.async_show_form(
            step_id="device",
            description_placeholders={
                "name": str(device.get("name") or imei),
                "imei": imei,
                "remaining": str(len(self._pending)),
            },
            data_schema=_device_form_schema(defaults, imei, "", True),
            errors={},
        )

    def _async_finish(self) -> ConfigFlowResult:
        """Persist the selection and all per-device settings."""
        entry = self.config_entry
        mc_types = {
            imei: next((d["mc_type"] for d in self._devices if d["imei"] == imei), "")
            for imei in self._settings
        }
        new_options = dict(entry.options)
        new_options.update(
            {
                CONF_DEVICE_IMEI: self._selected,
                CONF_MC_TYPE: mc_types,
                CONF_DEVICE_SETTINGS: dict(self._settings),
            }
        )
        return self.async_create_entry(title="", data=new_options)