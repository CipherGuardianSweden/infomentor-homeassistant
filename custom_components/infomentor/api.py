"""InfoMentor-klient (aiohttp)."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
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


_NAV_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
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
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "InfoMentor: %s %s skickar cookies: %s",
                method, url[:80], sorted(self._session.cookie_jar.filter_cookies(URL(url))),
            )
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

    @staticmethod
    def _redirect_headers(headers: MappingLike, new_url: str) -> MappingLike:
        """Headers för nästa hopp – som en webbläsare: behåll Accept och Referer.

        Hubben kraschar (302 → /Home/Errors/Server) på LoginCallback om Referer
        saknas. Webbläsaren skickar den ursprungliga Referer på varje hopp, men
        kortar den till enbart origin när målet är en annan host.
        """
        kept = {k: v for k, v in headers.items() if k in ("Accept", "Upgrade-Insecure-Requests")}
        referer = headers.get("Referer")
        if referer:
            ref = URL(referer)
            same_host = ref.host == URL(new_url).host
            kept["Referer"] = referer if same_host else f"{ref.scheme}://{ref.host}/"
        return kept

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
                _LOGGER.debug(
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
                        request_headers = self._redirect_headers(request_headers, url)
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
            _LOGGER.debug(
                "InfoMentor: %s svarade %s → %s", path, status, target.replace(HUB_BASE, "")
            )
            if "HandleUnauthorizedRequest" in target:
                # Saknar behörighet till modulen (t.ex. uppgifter) – inte en död session.
                raise ApiError(f"{path}: saknar behörighet ({target.replace(HUB_BASE, '')})")
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

    # ------------------------------------------------------------- inloggning
    async def async_login(self) -> list[dict[str, Any]]:
        """Loggar in och returnerar barnen. Kastar InvalidAuth/CannotConnect.

        Flödet är detsamma som i webbläsaren (verifierat mot en HAR-inspelning):
          1. GET  infomentor.se/.../mentor/          → inloggningsformulär
          2. POST formuläret                         → 302 till hubbens login-sida
          3. Hubbens login-sida innehåller ett auto-postande formulär med
             oauth_token → POST till infomentor.se   → 302 till LoginCallback
          4. LoginCallback sätter IMHome-cookien     → 302 till hubben

        Steg 3 måste göras manuellt (webbläsaren kör det via JavaScript); utan
        det sätts aldrig IMHome och alla hub-anrop svarar 302.

        Börjar alltid med en ren cookie-jar, annars kan en halv/gammal session
        göra att vi aldrig hittar inloggningsformuläret.
        """
        self._session.cookie_jar.clear()

        # STEG 1: Hämta inloggningsformuläret
        _, html = await self._follow(MENTOR_LOGIN, headers=_NAV_HEADERS)

        view_state = hidden_input(html, "__VIEWSTATE")
        if not view_state:
            raise InvalidAuth("nådde aldrig inloggningsformuläret")

        # STEG 2: Skicka med ALLA dolda fält (kommun-/IdP-listan) och credentials
        fields = find_login_fields(html)
        form = hidden_inputs(html)
        form[fields["username"]] = self._username
        form[fields["password"]] = self._password
        if fields.get("submit"):
            form[fields["submit"]] = "Logga in"
        form["__EVENTTARGET"] = ""
        form["__EVENTARGUMENT"] = ""

        _, html = await self._follow(
            MENTOR_LOGIN,
            method="POST",
            data=form,
            headers={
                **_NAV_HEADERS,
                "Origin": "https://infomentor.se",
                "Referer": MENTOR_LOGIN,
                "Sec-Fetch-Site": "same-origin",
            },
        )

        # STEG 3: Hubbens login-sida har oauth_token som ska postas tillbaka (som webbläsarens JS gör)
        oauth = extract_oauth_token(html)
        if oauth:
            _, html = await self._follow(
                MENTOR_LOGIN,
                method="POST",
                data={"oauth_token": oauth},
                headers={**_NAV_HEADERS, "Origin": HUB_BASE, "Referer": f"{HUB_BASE}/", "Sec-Fetch-Site": "same-site"},
            )
        callback = extract_callback_url(html)
        if callback:
            _LOGGER.debug("Följer LoginCallback: %s", callback[:120])
            await self._follow(callback, headers=_NAV_HEADERS)

        # STEG 4: Verifiera sessionen
        await self._follow(
            f"{HUB_BASE}/authentication/authentication/isauthenticated/?_={int(time() * 1000)}",
            method="POST",
            headers={"Origin": HUB_BASE, "Referer": f"{HUB_BASE}/", "X-Requested-With": "XMLHttpRequest"},
        )

        _, root = await self._follow(f"{HUB_BASE}/", headers=_NAV_HEADERS)
        if "selectedPupilName" not in root:
            cookie_names = sorted({c.key for c in self._session.cookie_jar})
            _LOGGER.debug("Inloggning avvisad, cookies: %s", cookie_names)
            raise InvalidAuth("inloggningen avvisades")

        self.pupils = extract_pupils(root)
        _LOGGER.debug("Inloggad, %d barn hittade", len(self.pupils))
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
