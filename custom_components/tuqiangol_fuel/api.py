"""API client for the Tuqiangol web console.

One ``TuqiangolAPI`` instance represents one logged-in account. The login
state lives in a ``SHAREJSESSIONID`` cookie, so every instance must be given
its own ``aiohttp.ClientSession``. A cookie guard is kept as a safety net:
if another account replaces the cookie, the next request logs in again
instead of silently using the foreign session.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20
LOGIN_HINT = "/index.jsp"
REDIRECT_CODES = (301, 302, 303, 307, 308)
SESSION_COOKIE = "SHAREJSESSIONID"


class TuqiangolError(Exception):
    """Base error."""


class TuqiangolAuthError(TuqiangolError):
    """Authentication failed."""


class TuqiangolConnectionError(TuqiangolError):
    """Cannot reach the cloud platform."""


def encode_password(password: str) -> str:
    """Encode the password the same way the web console does."""
    return "|".join(str(ord(char)) for char in password)


def normalize_base_url(base_url: str) -> str:
    """Strip whitespace/trailing slash and add ``https://`` when missing.

    Users often type ``www.tuqiangol.com`` without a scheme; without this the
    request URL would be malformed and only produce an unhelpful error.
    """
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    if "://" not in url:
        url = "https://" + url
    return url


def _label_is_plain(label: str, keyword: str) -> bool:
    """True when the label is the plain option for ``keyword``.

    The platform uses "断油路" / "恢复油路". Only those plain forms (and the
    equally plain "断油" / "恢复" spellings) are accepted, so a decorated
    variant such as "恢复油路(延时)" can never shadow the real switch option.
    """
    text = label.strip()
    return text in {
        keyword,
        f"{keyword}路",
        f"{keyword}电",
        f"{keyword}开关",
    }


class TuqiangolAPI:
    """Minimal client covering login, devices, fuel records and sendIns."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        account: str,
        password: str,
    ) -> None:
        self._session = session
        self.base_url = normalize_base_url(base_url)
        self.account = account
        self.password = password
        self.user_id: str | None = None
        self.user_type: int = 3
        self._logged_in = False
        self._in_login = False
        self._session_cookie: str | None = None
        self._login_lock = asyncio.Lock()

    # ------------------------------------------------------------- cookie
    def _current_cookie(self) -> str | None:
        """Return the session cookie currently stored for this host."""
        host = self.base_url.split("//")[-1].split("/")[0].split(":")[0].lower()
        for cookie in self._session.cookie_jar:
            if cookie.key != SESSION_COOKIE:
                continue
            domain = str(cookie["domain"] or "").lstrip(".").lower()
            if domain in ("", host):
                return str(cookie.value)
        return None

    def _cookie_changed(self) -> bool:
        """Detect another account having replaced the session cookie."""
        if self._session_cookie is None:
            return False
        current = self._current_cookie()
        return current is not None and current != self._session_cookie

    # ------------------------------------------------------------------ http
    async def _post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        referer: str | None = None,
        _retry: bool = True,
    ) -> dict[str, Any]:
        """POST a form and decode JSON, re-authenticating once on expiry."""
        if (
            _retry
            and not self._in_login
            and path not in ("/api/regdc", "/getNewDomain")
            and self._cookie_changed()
        ):
            _LOGGER.debug("会话 cookie 已被替换，重新登录后重试 %s", path)
            async with self._login_lock:
                if not self._in_login and self._cookie_changed():
                    self._logged_in = False
                    await self._async_login_locked()
            return await self._post(path, data, referer, _retry=False)

        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": self.base_url,
            "Referer": referer or f"{self.base_url}/console",
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with self._session.post(
                url,
                data=data or {},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=False,
            ) as resp:
                status = resp.status
                location = resp.headers.get("Location", "")
                text = await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            # some aiohttp errors stringify to an empty string, which would make
            # the log line useless; always include the exception type
            detail = str(err).strip() or type(err).__name__
            raise TuqiangolConnectionError(f"{path}: {detail}") from err

        if status in REDIRECT_CODES or (location and LOGIN_HINT in location):
            # The console answers http:// with a redirect to https://
            if _retry and self._upgrade_scheme(location):
                return await self._post(path, data, referer, _retry=False)
            # Inside login() a redirect means the server refuses us; retrying
            # there would recurse between _post() and login() forever.
            if _retry and not self._in_login:
                _LOGGER.debug("登录态失效，重新登录后重试 %s", path)
                async with self._login_lock:
                    if not self._in_login and self._logged_in:
                        self._logged_in = False
                        await self._async_login_locked()
                return await self._post(path, data, referer, _retry=False)
            raise TuqiangolAuthError(
                f"{path} 被重定向到 {location or status}"
                "（账号密码或站点地址有误，或登录态已失效）"
            )

        if status >= 400:
            raise TuqiangolError(f"HTTP {status} on {path}: {text[:200]}")

        try:
            payload = json.loads(text)
        except ValueError as err:
            raise TuqiangolError(f"{path} returned non-JSON: {text[:200]}") from err
        if not isinstance(payload, dict):
            raise TuqiangolError(f"{path} returned unexpected payload")
        return payload

    def _upgrade_scheme(self, location: str) -> bool:
        """Upgrade an http base URL to https when the server redirects there."""
        if not location or not location.lower().startswith("https://"):
            return False
        if not self.base_url.lower().startswith("http://"):
            return False
        old_host = self.base_url[len("http://") :].split("/")[0].lower()
        new_host = location[len("https://") :].split("/")[0].lower()
        if old_host != new_host:
            return False
        self.base_url = "https://" + self.base_url[len("http://") :]
        _LOGGER.info("站点地址已自动升级为 %s", self.base_url)
        return True

    # ------------------------------------------------------------------ login
    async def login(self) -> None:
        """Authenticate and remember the account id."""
        async with self._login_lock:
            if self._logged_in:
                return
            await self._async_login_locked()

    async def _async_login_locked(self) -> None:
        """Login body; the caller must already hold ``_login_lock``."""
        self._in_login = True
        try:
            await self._post(
                "/getNewDomain",
                {"domainId": self.base_url.split("//")[-1].strip("/")},
                referer=f"{self.base_url}/index.jsp",
            )
            result = await self._post(
                "/api/regdc",
                {
                    "ver": "1",
                    "method": "login",
                    "account": self.account,
                    "password": encode_password(self.password),
                    "language": "zh",
                },
                referer=f"{self.base_url}/index.jsp",
            )
            if result.get("code") != 0:
                raise TuqiangolAuthError(str(result.get("msg") or result))

            profile = await self._post(
                "/customer/getProviderList",
                {},
                referer=f"{self.base_url}/nomalUserCenter",
            )
            if profile.get("code") != 0:
                raise TuqiangolAuthError(str(profile.get("msg") or profile))

            user = (profile.get("data") or {}).get("user") or {}
            self.user_id = str(user.get("userId") or "")
            self.user_type = int(user.get("type") or 3)
            self._session_cookie = self._current_cookie()
            self._logged_in = True
            _LOGGER.debug("Tuqiangol 登录成功 userId=%s", self.user_id)
        except TuqiangolError:
            self._logged_in = False
            self.user_id = None
            self._session_cookie = None
            raise
        finally:
            self._in_login = False

    async def _ensure_login(self) -> None:
        if not self._logged_in or not self.user_id:
            await self.login()

    # ---------------------------------------------------------------- devices
    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return every device of the account (imei/name/mc_type)."""
        await self._ensure_login()
        groups = await self._post(
            "/console/getUserGroup",
            {"userId": self.user_id, "type": "NORMAL", "userType": self.user_type},
        )
        if groups.get("code") != 0:
            raise TuqiangolError(str(groups.get("msg") or groups))

        devices: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group in groups.get("data") or []:
            result = await self._post(
                "/console/queryEquipmentList",
                {
                    "orgId": group.get("id", ""),
                    "type": "NORMAL",
                    "startRow": "0",
                    "keyword": "",
                    "imei": "",
                    "userType": self.user_type,
                    "userId": self.user_id,
                },
            )
            if result.get("code") != 0:
                raise TuqiangolError(str(result.get("msg") or result))
            for dev in result.get("data") or []:
                imei = str(dev.get("imei") or "").strip()
                if not imei or imei in seen:
                    continue
                seen.add(imei)
                devices.append(
                    {
                        "imei": imei,
                        "name": dev.get("deviceName") or imei,
                        "mc_type": dev.get("mcType") or "",
                        "online": str(dev.get("equipNet") or "") == "1",
                    }
                )
        return devices

    # ----------------------------------------------------------- instructions
    async def async_get_fuel_instruction(
        self, imei: str, mc_type: str, type_id: str
    ) -> dict[str, Any]:
        """Resolve the fuel-relay instruction definition for this device."""
        await self._ensure_login()
        result = await self._post(
            "/device/getInstructionAndParams",
            {"imei": imei, "mcType": mc_type, "typeId": type_id},
        )
        if result.get("code") != 0:
            raise TuqiangolError(str(result.get("msg") or result))

        data = result.get("data") or {}
        for instruction in data.get("instructionAndParams") or []:
            name = instruction.get("orderName") or ""
            if "油路" in name:
                return instruction
        raise TuqiangolError(
            f"设备 {imei}（型号 {mc_type or '未知'}）未提供断油路指令"
        )

    @staticmethod
    def _param_value(instruction: dict[str, Any], keyword: str) -> tuple[str, str]:
        """Return (param_id, value) for the option matching ``keyword``.

        An exact label match wins over a substring match, so a future option
        such as "恢复油路(延时)" can never shadow the plain "恢复油路".
        """
        exact: list[tuple[str, str]] = []
        partial: list[tuple[str, str]] = []
        for param in instruction.get("paramList") or []:
            param_id = str(param.get("id"))
            for item in str(param.get("paramVals") or "").split(";"):
                if ":" not in item:
                    continue
                label, value = item.split(":", 1)
                label = label.strip()
                value = value.strip()
                if not label or not value:
                    continue
                if _label_is_plain(label, keyword):
                    exact.append((param_id, value))
                elif keyword in label:
                    partial.append((param_id, value))

        for candidate in (exact, partial):
            if candidate:
                return candidate[0]
        raise TuqiangolError(f"指令参数中找不到包含“{keyword}”的可选项")

    async def async_send_fuel(
        self,
        action: str,
        imei: str,
        mc_type: str,
        type_id: str,
        instruction_password: str,
        fuel_param_id: str | None = None,
    ) -> dict[str, Any]:
        """Send the cut (``cut``) or restore (``restore``) instruction."""
        from .const import (
            FUEL_PARAM_KEYWORD_CUT,
            FUEL_PARAM_KEYWORD_RESTORE,
            STATE_CUT,
        )

        keyword = (
            FUEL_PARAM_KEYWORD_CUT if action == STATE_CUT else FUEL_PARAM_KEYWORD_RESTORE
        )
        instruction = await self.async_get_fuel_instruction(imei, mc_type, type_id)
        param_id, value = self._param_value(instruction, keyword)
        if fuel_param_id:
            # A stale or mistyped override used to be sent verbatim, which the
            # platform accepts but which then targets the wrong parameter.
            valid_ids = {
                str(param.get("id")) for param in instruction.get("paramList") or []
            }
            if str(fuel_param_id) not in valid_ids:
                _LOGGER.warning(
                    "配置的油路参数 ID %s 不属于该设备的指令参数 %s，改用自动识别",
                    fuel_param_id,
                    sorted(valid_ids),
                )
            else:
                param_id = str(fuel_param_id)

        payload = {
            "imei": imei,
            "orderContent": instruction.get("orderContent") or "",
            "instructionId": str(instruction.get("id") or ""),
            "instructionName": instruction.get("orderName") or "",
            "param": f"{param_id},{value}",
            "instructionPwd": instruction_password,
            "isUsePwd": str(instruction.get("isUsePwd") or "1"),
            "isOffLine": str(instruction.get("isOffLine") or "1"),
        }
        # Never log the whole payload: instructionPwd defaults to the account
        # login password, so a debug log would leak it into bug reports.
        _LOGGER.debug(
            "Tuqiangol sendIns: imei=%s instructionId=%s param=%s",
            payload["imei"],
            payload["instructionId"],
            payload["param"],
        )
        return await self._post("/device/sendIns", payload)

    # -------------------------------------------------------------- records
    async def async_get_records(self, imei: str) -> list[dict[str, Any]]:
        """Return the parameter history of one device."""
        await self._ensure_login()
        result = await self._post(
            "/device/getCommandRecord", {"userId": "", "imei": imei}
        )
        if result.get("code") != 0:
            raise TuqiangolError(str(result.get("msg") or result))
        data = result.get("data")
        if isinstance(data, list):
            return data
        if data:
            _LOGGER.warning("设备 %s 的指令记录格式异常: %s", imei, type(data).__name__)
        return []
