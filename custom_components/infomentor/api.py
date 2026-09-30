"""InfoMentor-klient (aiohttp)."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from time import time
from typing import Any

import aiohttp
from yarl import URL

from .const import HUB_BASE, MATEO_API, MENTOR_LOGIN
from .util import (
    extract_callback_url,
    extract_oauth_token,
    extract_pupils,
    find_login_fields,
    hidden_input,
    hidden_inputs,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
MAX_HOPS = 20
_REDIRECTS = {301, 302, 303, 307, 308}

_BROWSER_HEADERS = {
    "Accept-Language": "sv-SE,sv;q=0.8",
    "sec-ch-ua": '"Chromium";v="154", "Brave";v="154", "Not A(Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-GPC": "1",
}

MappingLike = dict[str, Any]


class InfomentorError(Exception):
    """Bas för integrationsfel."""


class CannotConnect(InfomentorError):
    """Kunde inte nå InfoMentor."""


class InvalidAuth(InfomentorError):
    """Fel inloggningsuppgifter eller död session."""


class ApiError(InfomentorError):
    """En endpoint svarade oväntat — *inte* ett autentiseringsproblem."""


class InfomentorApi:
    """Tunn klient mot hub.infomentor.se."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self.pupils: list[dict[str, Any]] = []

    # ------------------------------------------------------------- transport
    async def _once(
        self, url: str, *, method: str = "GET", data: Any = None, headers: MappingLike | None = None
    ) -> aiohttp.ClientResponse:
        request_headers = {
            "User-Agent": USER_AGENT,
            **_BROWSER_HEADERS,
            **(headers or {}),
        }
        try:
            return await self._session.request(
                method,
                url,
                data=data,
                headers=request_headers,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise CannotConnect(str(err)) from err

    async def _follow(
        self,
        url: str,
        *,
        method: str = "GET",
        data: Any = None,
        headers: MappingLike | None = None,
    ) -> tuple[str, str]:
        """Följer omdirigeringar manuellt och returnerar (slutlig_url, body)."""
        request_headers = dict(headers or {})
        for hop in range(MAX_HOPS):
            response = await self._once(
                url, method=method, data=data, headers=request_headers
            )
            try:
                location = response.headers.get("Location", "")
                _LOGGER.warning(
                    "InfoMentor[hop %d]: %s %s → %s (location: %s)",
                    hop, method, url[:100], response.status,
                    location[:120] if location else "(ingen)",
                )
                if response.status in _REDIRECTS:
                    if not location:
                        return str(response.url), await response.text()
                    url = str(response.url.join(URL(location)))
                    if response.status in (302, 303):
                        method, data = "GET", None
                        request_headers = {}
                    continue
                text = await response.text()
                return str(response.url), text
            finally:
                response.release()
        raise CannotConnect("för många omdirigeringar")

    async def _post_hub(self, path: str, body: Any | None = None, *, _retried: bool = False) -> Any:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": HUB_BASE,
            "Referer": f"{HUB_BASE}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        payload = json.dumps(body) if body else None
        url = f"{HUB_BASE}{path}"
        response = await self._once(url, method="POST", data=payload, headers=headers)
        try:
            status = response.status
            location = response.headers.get("Location") or ""
            final_url = str(response.url)
            text = await response.text()
        finally:
            response.release()

        if status in (401, 403):
            raise InvalidAuth(f"HTTP {status} på {path}")

        if status in {301, 302, 303, 307, 308}:
            target = str(URL(final_url).join(URL(location))) if location else "(okänd)"
            _LOGGER.warning(
                "InfoMentor: %s svarade %s → %s", path, status, target.replace(HUB_BASE, "")
            )
            if location and not _retried:
                await self._follow(target)
                return await self._post_hub(path, body, _retried=True)
            raise InvalidAuth(f"{path} omdirigerade till {target}")
        if not text.strip():
            raise InvalidAuth("tomt svar – sessionen har gått ut")

        if status >= 400:
            raise ApiError(f"{path} svarade {status}: {text[:120]!r}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as err:
            raise ApiError(f"{path} gav ogiltigt svar: {text[:120]!r}") from err

    # ------------------------------------------------------------- cookie-dump
    def _dump_cookies(self, label: str) -> None:
        """Sparar alla cookies till /config för analys."""
        try:
            data = [
                {
                    "key": c.key,
                    "value": c.value,
                    "domain": c.get("domain", ""),
                    "path": c.get("path", "/"),
                    "expires": str(c.get("expires", "session")),
                    "size": len(c.value),
                }
                for c in self._session.cookie_jar
            ]
            path = Path(f"/config/infomentor_cookies_{label}.json")
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            _LOGGER.warning(
                "InfoMentor: %d cookies sparade till %s",
                len(data), path.name,
            )
        except Exception as err:
            _LOGGER.warning("InfoMentor: kunde inte spara cookies '%s': %s", label, err)

    # ------------------------------------------------------------- inloggning
    async def async_login(self) -> list[dict[str, Any]]:
        """Loggar in och returnerar barnen."""
        self._session.cookie_jar.clear()

        # STEG 1: Hämta formuläret från infomentor.se
        _, html = await self._follow(
            MENTOR_LOGIN,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        self._dump_cookies("01_after_get_form")

        # STEG 2: Extrahera formulärfält
        view_state = hidden_input(html, "__VIEWSTATE")
        if not view_state:
            raise InvalidAuth("nådde aldrig inloggningsformuläret")

        fields = find_login_fields(html)
        form = hidden_inputs(html)

        idp_count = sum(1 for k in form if "IdpListRepeater" in k and k.endswith("$url"))
        _LOGGER.warning(
            "InfoMentor: formulär har %d fält, varav %d IDP-val", len(form), idp_count
        )

        # STEG 3: Fyll i credentials
        form[fields["username"]] = self._username
        form[fields["password"]] = self._password
        if fields.get("submit"):
            form[fields["submit"]] = "Logga in"
        form["__EVENTTARGET"] = ""
        form["__EVENTARGUMENT"] = ""

        # STEG 4: POST hela formuläret
        _, html = await self._follow(
            MENTOR_LOGIN,
            method="POST",
            data=form,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://infomentor.se",
                "Referer": MENTOR_LOGIN,
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        self._dump_cookies("02_after_post")

        # STEG 5: Följ LoginCallback
        callback = extract_callback_url(html)
        if callback:
            _LOGGER.warning("InfoMentor: följer LoginCallback: %s", callback[:120])
            _, html = await self._follow(callback)
        else:
            _LOGGER.warning("InfoMentor: ingen LoginCallback hittades i svaret")
        self._dump_cookies("03_after_callback")

        cookie_names = {c.key for c in self._session.cookie_jar}
        if "IMHome" not in cookie_names:
            raise InvalidAuth(
                f"IMHome-cookien saknas. Cookies: {sorted(cookie_names)}"
            )

        # STEG 6: Verifiera isauthenticated
        _, auth_body = await self._follow(
            f"{HUB_BASE}/authentication/authentication/isauthenticated/?_={int(time() * 1000)}",
            method="POST",
            data="null",
            headers={
                "Accept": "*/*",
                "cache-control": "no-cache",
                "Origin": HUB_BASE,
                "Referer": f"{HUB_BASE}/",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        _LOGGER.warning("InfoMentor: isauthenticated svar = %r", auth_body[:80])
        self._dump_cookies("04_after_isauthenticated")

        if auth_body.strip().lower() != "true":
            raise InvalidAuth(f"isauthenticated svarade {auth_body[:80]!r}")

        _, root = await self._follow(f"{HUB_BASE}/")
        self._dump_cookies("05_after_root")

        if "selectedPupilName" not in root:
            raise InvalidAuth("inloggningen avvisades")

        self.pupils = extract_pupils(root)
        _LOGGER.warning("InfoMentor: Inloggad, %d barn hittade", len(self.pupils))
        return self.pupils

    # ------------------------------------------------------------- endpoints
    async def async_switch_pupil(self, pupil: MappingLike) -> None:
        url = pupil.get("switchPupilUrl")
        if not url:
            raise InfomentorError("barnet saknar switchPupilUrl")
        await self._follow(
            str(url),
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": HUB_BASE,
                "Referer": f"{HUB_BASE}/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-site",
            },
        )

    async def async_lessons(self, pupil: MappingLike, *, days: int = 7) -> list[dict[str, Any]]:
        today = date.today()
        data = await self._post_hub(
            "/timetable/timetable/gettimetablelist",
            {"UTCOffset": "-120", "start": today.isoformat(),
             "end": (today + timedelta(days=days)).isoformat()},
        )
        return data if isinstance(data, list) else data.get("items", [])

    async def async_calendar(self, pupil: MappingLike, *, days: int = 30) -> list[dict[str, Any]]:
        today = date.today()
        data = await self._post_hub(
            "/calendarv2/calendarv2/getentries",
            {"startDate": today.isoformat(), "endDate": (today + timedelta(days=days)).isoformat()},
        )
        return data if isinstance(data, list) else []

    async def async_tasks(self, pupil: MappingLike) -> list[dict[str, Any]]:
        data = await self._post_hub("/task/task/GetTasks")
        return data.get("items", []) if isinstance(data, dict) else []

    async def async_attendance(self, pupil: MappingLike) -> dict[str, Any]:
        data = await self._post_hub("/attendance/attendance/appData")
        return data if isinstance(data, dict) else {}

    async def async_learnlog(self, pupil: MappingLike) -> dict[str, Any]:
        data = await self._post_hub("/learnlog/learnlog/appData")
        return data if isinstance(data, dict) else {}

    async def async_plan_detail(self, uol_id: str) -> dict[str, Any]:
        data = await self._post_hub("/UolV2/UolV2/GetUol", {"id": uol_id})
        return data if isinstance(data, dict) else {}

    async def async_plans(self, pupil: MappingLike) -> dict[str, Any]:
        data = await self._post_hub("/UolV2/UolV2/GetUols")
        return data if isinstance(data, dict) else {}

    async def async_notifications(self) -> list[dict[str, Any]]:
        data = await self._post_hub("/NotificationApp/NotificationApp/appData")
        return data.get("notifications", []) if isinstance(data, dict) else []

    async def async_news(self) -> list[dict[str, Any]]:
        data = await self._post_hub(
            "/Communication/News/GetNewsList",
            {"pageSize": -1, "sortBy": "lastPublishDate___SORT_DESC"},
        )
        return data.get("items", []) if isinstance(data, dict) else []

    async def async_lunch(self, unit_id: str, *, days: int = 14) -> list[dict[str, Any]]:
        today = date.today()
        url = f"{MATEO_API}/{unit_id}?from={today.isoformat()}&to={(today + timedelta(days=days)).isoformat()}"
        response = await self._once(
            url, headers={"Accept": "application/json", "Referer": "https://meny.mateo.se/"}
        )
        try:
            if response.status != 200:
                raise InfomentorError(f"Mateo svarade {response.status}")
            return await response.json(content_type=None)
        finally:
            response.release()
