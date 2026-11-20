"""Data coordinator for Tuqiangol fuel control."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import settings as dev_settings
from .api import TuqiangolAPI, TuqiangolAuthError, TuqiangolError
from .const import (
    ALREADY_IN_STATE_CUT,
    ALREADY_IN_STATE_MARKERS,
    ALREADY_IN_STATE_RESTORE,
    CODE_OFFLINE_ERROR,
    CODE_OFFLINE_SAVED,
    CODE_OK,
    DOMAIN,
    FUEL_RECORD_MARKER,
    STATE_CUT,
    STATE_RESTORE,
)

_LOGGER = logging.getLogger(__name__)

CONFIRM_TIMEOUT = 20   # seconds to wait for the platform to store a new record
CONFIRM_INTERVAL = 3

# Only these codes mean the platform accepted the command.
ACCEPTED_CODES = (CODE_OK, CODE_OFFLINE_SAVED)

# How many record ids are remembered per device. The platform returns only a
# short history, so this comfortably covers every record a response can carry
# while keeping memory flat over a long uptime.
MAX_TRACKED_IDS = 200


class TuqiangolCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll the platform command records and mirror the fuel state.

    Platform quirks handled here:

    1. ``/device/getCommandRecord`` returns records in an unspecified order, so
       the newest fuel record is selected by its timestamp.
    2. Timestamps have one second resolution, so several commands can share the
       same timestamp and the platform offers no way to tell which came last.
       Records are therefore tracked by their unique ``id``: only an id that has
       never been seen may change the state, so a stale record can never make
       the switch bounce back.
    3. When the newest group is ambiguous, the state produced by *our own*
       command wins, because that state is known for certain.

    Polling and device parameters are resolved per device, so each entry can
    expose devices with different intervals and instruction settings.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: TuqiangolAPI,
        imeis: list[str],
        device_map: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.api = api
        self.imeis = imeis
        self.entry = entry
        self._device_map = device_map or {}
        self._state: dict[str, str | None] = {}
        # imei -> ordered {record_id: state}; serves as both the "seen" set and
        # the id->state map, so trimming them can never disagree
        self._id_state: dict[str, dict[str, str]] = {}
        self._pending_action: dict[str, str] = {}
        # imei -> True when the most recent poll for that device succeeded
        self._available: dict[str, bool] = {}
        # one lock per device: a slow terminal must not block the others
        self._locks: dict[str, asyncio.Lock] = {}
        # imei -> loop time of the last successful read (per-device intervals)
        self._last_poll: dict[str, float] = {}
        # a single coordinator polls every device, so it runs at the fastest
        # interval any device asked for
        interval = dev_settings.max_supported_interval(entry, imeis)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    # ------------------------------------------------------------------ poll
    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Poll every device, isolating per-device failures.

        A device that cannot be read keeps its last known value instead of
        taking the whole integration (and all other devices) down with it.
        """
        data: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        due = self._due_devices()

        for imei in self.imeis:
            if imei not in due:
                previous = (self.data or {}).get(imei)
                data[imei] = previous or {"state": None, "record": None}
                continue
            try:
                data[imei] = await self._async_read_state(imei, adopt_new=True)
            except UpdateFailed as err:
                failures.append(str(err))
                previous = (self.data or {}).get(imei)
                data[imei] = previous or {"state": None, "record": None}
                self._available[imei] = False
                _LOGGER.warning("设备 %s 状态读取失败，保留上次状态: %s", imei, err)
            else:
                self._available[imei] = True
                self._last_poll[imei] = asyncio.get_running_loop().time()

        if due and failures and len(failures) == len(due):
            raise UpdateFailed("; ".join(failures))
        return data

    def _due_devices(self) -> set[str]:
        """Devices whose own interval has elapsed since the previous read."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        base = dev_settings.max_supported_interval(self.entry, self.imeis)
        due: set[str] = set()
        for imei in self.imeis:
            interval = dev_settings.scan_interval(self.entry, imei)
            last = self._last_poll.get(imei)
            if last is None or now - last >= interval - (base / 2):
                due.add(imei)
        return due

    async def _async_read_state(
        self, imei: str, adopt_new: bool
    ) -> dict[str, Any]:
        """Read one device and adopt an unseen, unambiguous newest record."""
        try:
            records = await self.api.async_get_records(imei)
        except TuqiangolAuthError as err:
            # The stored password no longer works (changed on the platform, or
            # the account is locked). Retrying every poll would keep hammering
            # the account, so stop polling and let Home Assistant start the
            # reauth flow that already exists in the config flow.
            raise ConfigEntryAuthFailed(f"{imei}: {err}") from err
        except TuqiangolError as err:
            raise UpdateFailed(f"{imei}: {err}") from err

        # same lookup order as the send path, otherwise polling and sending
        # could disagree about which parameter identifies the fuel switch
        param_id = dev_settings.fuel_param_id(self.entry, imei) or None
        fuel_records = self._fuel_records(records, param_id)
        newest = self._newest_group(fuel_records)
        present_ids = [
            record_id
            for record_id in (str(record.get("id") or "") for record in newest)
            if record_id
        ]

        if present_ids:
            known = self._id_state.setdefault(imei, {})
            fresh = [record_id for record_id in present_ids if record_id not in known]

            if fresh and adopt_new:
                pending = self._pending_action.pop(imei, None)
                states = {self._record_state(record) for record in newest} - {None}

                if len(states) == 1:
                    resolved = states.pop()
                elif pending is not None:
                    # our own command created one of these; it is authoritative
                    resolved = pending
                    _LOGGER.debug(
                        "设备 %s 同一秒内有多条记录，采用本次下发状态 %s", imei, pending
                    )
                else:
                    # ignore placeholder entries ("" = id seen, state unknown)
                    known_states = {
                        known[i] for i in present_ids if known.get(i)
                    }
                    resolved = known_states.pop() if len(known_states) == 1 else None

                if resolved is not None:
                    self._state[imei] = resolved
                    for record_id in fresh:
                        known[record_id] = resolved
                else:
                    _LOGGER.debug(
                        "设备 %s 同一秒内有多条互相冲突的油路记录，状态未知", imei
                    )

            # Trim only AFTER fresh-detection, and never drop an id that is
            # still present in this response (that would make it look new and
            # could bounce the state on the next poll).
            self._trim(known, present_ids)

        return {
            "state": self._state.get(imei),
            "record": self._newest(fuel_records),
        }

    def async_is_available(self, imei: str) -> bool:
        """Whether the last poll for this specific device succeeded."""
        return self._available.get(imei, True)

    @staticmethod
    def _trim(known: dict[str, str], present_ids: list[str]) -> None:
        """Bound the id cache, keeping every id still seen in the response."""
        if len(known) <= MAX_TRACKED_IDS:
            return
        keep = set(present_ids)
        for record_id in list(known):
            if len(known) <= MAX_TRACKED_IDS:
                break
            if record_id in keep:
                continue
            known.pop(record_id, None)

    @staticmethod
    def _stamp(record: dict[str, Any]) -> str:
        return str(record.get("updateTime") or record.get("creationDate") or "")

    @staticmethod
    def _fuel_records(
        records: list[dict[str, Any]], param_id: str | None
    ) -> list[dict[str, Any]]:
        result = []
        for record in records:
            value = str(record.get("paramRecord") or "")
            if param_id:
                if str(record.get("paramId")) != str(param_id):
                    continue
            elif FUEL_RECORD_MARKER not in value:
                continue
            result.append(record)
        return result

    @classmethod
    def _newest_group(cls, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Records sharing the newest timestamp."""
        if not records:
            return []
        latest = max(cls._stamp(record) for record in records)
        return [record for record in records if cls._stamp(record) == latest]

    @classmethod
    def _newest(cls, records: list[dict[str, Any]]) -> dict[str, Any] | None:
        group = cls._newest_group(records)
        return group[-1] if group else None

    @staticmethod
    def _record_state(record: dict[str, Any]) -> str | None:
        value = str(record.get("paramRecord") or "")
        if value.endswith(":1"):
            return STATE_CUT
        if value.endswith(":0"):
            return STATE_RESTORE
        return None

    # --------------------------------------------------------------- actions
    async def async_send_fuel(self, action: str, imei: str) -> dict[str, Any]:
        """Send the command and show the requested state immediately."""
        type_id = dev_settings.instruction_type_id(self.entry, imei)
        # same fallback chain as setup, otherwise a device whose model is only
        # known from the account device list would fail on every command
        mc_type = dev_settings.mc_type(self.entry, imei) or (
            self._device_map.get(imei) or {}
        ).get("mc_type", "")
        instruction_password = dev_settings.instruction_password(
            self.entry, imei, self.api.password
        )
        param_id = dev_settings.fuel_param_id(self.entry, imei) or None

        # one command at a time per device, so the new record can be identified
        async with self._locks.setdefault(imei, asyncio.Lock()):
            try:
                existing = self._fuel_records(
                    await self.api.async_get_records(imei), param_id
                )
            except TuqiangolError as err:
                _LOGGER.debug("读取下发前记录失败: %s", err)
                existing = []
            known = self._id_state.setdefault(imei, {})
            for record in existing:
                record_id = str(record.get("id") or "")
                if record_id and record_id not in known:
                    known[record_id] = ""

            result = await self.api.async_send_fuel(
                action, imei, mc_type, type_id, instruction_password, param_id
            )

            # ``ok`` is also true for failures such as 10006, so the code is
            # what decides whether the command was really accepted.
            if result.get("code") not in ACCEPTED_CODES:
                if result.get("code") == CODE_OFFLINE_ERROR:
                    _LOGGER.error("设备 %s 离线入队失败，指令未生效", imei)
                return result

            # ``255`` also covers "terminal already in that state", where the
            # platform creates no new record. Waiting for one would block this
            # device (and the caller) for the whole confirm timeout.
            if self._already_in_state(action, result):
                _LOGGER.debug("设备 %s 已处于目标状态，无需等待新记录", imei)
                self._set_state(imei, action)
                return result

            # A reply naming the opposite state means the platform refused the
            # command. Adopting it optimistically would show the wrong state.
            if self._contradicts(action, result):
                _LOGGER.warning(
                    "设备 %s 的 %s 指令未被执行（终端处于相反状态），保持原状态",
                    imei,
                    action,
                )
                return result

            # accepted by the platform: reflect it right away
            self._pending_action[imei] = action
            self._set_state(imei, action)

            try:
                await self._async_wait_for_new_record(imei)
            finally:
                # always clear: a cancelled/failed wait must not leave a stale
                # pending action behind
                self._pending_action.pop(imei, None)
        return result

    def _set_state(self, imei: str, action: str) -> None:
        """Store one device state and notify entities."""
        self._state[imei] = action
        self.data.setdefault(imei, {})
        self.data[imei]["state"] = action
        self.async_update_listeners()

    @staticmethod
    def _reply_text(result: dict[str, Any]) -> str:
        """Lower-cased text of a platform reply, whatever its shape."""
        data = result.get("data")
        text = ""
        if isinstance(data, dict):
            text = str(data.get("msg") or (data.get("log") or {}).get("content") or "")
        elif data:
            text = str(data)
        if not text:
            text = str(result.get("msg") or "")
        return text.lower()

    def already_in_state(self, action: str, result: dict[str, Any]) -> bool:
        """Public wrapper used by the entity for a truthful result label."""
        return self._already_in_state(action, result)

    def contradicts(self, action: str, result: dict[str, Any]) -> bool:
        """Public wrapper used by the entity for a truthful result label."""
        return self._contradicts(action, result)

    @classmethod
    def _contradicts(cls, action: str, result: dict[str, Any]) -> bool:
        """True when the reply says the terminal is already in the *other* state."""
        text = cls._reply_text(result)
        if ALREADY_IN_STATE_CUT in text:
            return action == STATE_RESTORE
        if ALREADY_IN_STATE_RESTORE in text:
            return action == STATE_CUT
        return False

    @classmethod
    def _already_in_state(cls, action: str, result: dict[str, Any]) -> bool:
        """True when the reply says the terminal is already in *this* state.

        The reply also names which state the terminal is in, so a contradictory
        reply (e.g. asked to cut but told "already connected") is not treated as
        success - it falls through to the normal confirm path instead.
        """
        text = cls._reply_text(result)
        if not any(marker in text for marker in ALREADY_IN_STATE_MARKERS):
            return False
        if ALREADY_IN_STATE_CUT in text:
            return action == STATE_CUT
        if ALREADY_IN_STATE_RESTORE in text:
            return action == STATE_RESTORE
        # generic "will not be executed" without a state: trust the request
        return True

    async def _async_wait_for_new_record(self, imei: str) -> None:
        """Poll until a record with an unseen id appears (bounded wait)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONFIRM_TIMEOUT
        known = set(self._id_state.get(imei, {}))

        while True:
            try:
                payload = await self._async_read_state(imei, adopt_new=True)
            except UpdateFailed as err:
                _LOGGER.debug("等待状态确认时读取失败: %s", err)
            else:
                self.data[imei] = payload
                self.async_update_listeners()
                if set(self._id_state.get(imei, {})) - known:
                    return
            if loop.time() >= deadline:
                _LOGGER.debug("设备 %s 在 %s 秒内未出现新记录", imei, CONFIRM_TIMEOUT)
                return
            await asyncio.sleep(CONFIRM_INTERVAL)
