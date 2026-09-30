"""Tester för InfoMentor-klienten (kräver aiohttp; körs i CI)."""

from __future__ import annotations

import json
import unittest

try:
    import aiohttp  # noqa: F401
    import yarl

    from custom_components.infomentor.api import ApiError, InfomentorApi, InvalidAuth

    AVAILABLE = True
except Exception:  # noqa: BLE001
    AVAILABLE = False


HUB = "https://hub.infomentor.se/"
MENTOR = "https://infomentor.se/swedish/production/mentor/"

OAUTH_HTML = (
    '<form action="' + MENTOR + '" method="post">'
    '<input type="hidden" name="oauth_token" value="TOK" /></form>'
)
LOGIN_HTML = (
    "<form>"
    '<input type="hidden" name="__VIEWSTATE" value="VS" />'
    '<input type="hidden" name="__VIEWSTATEGENERATOR" value="GEN" />'
    '<input type="hidden" name="__EVENTVALIDATION" value="EV" />'
    '<input type="text" name="login_ascx$txtNotandanafn" />'
    '<input type="password" name="login_ascx$txtLykilord" />'
    '<input type="submit" name="login_ascx$btnLogin" value="Logga in" />'
    "</form>"
)
HUB_HTML = (
    "<script>IMHome={init:{\"pupils\":[{\"id\":\"111\",\"name\":\"Efternamn, Anna\","
    '"switchPupilUrl":"https://hub.infomentor.se/Account/PupilSwitcher/SwitchPupil/999",'
    "\"selected\":true}],selectedPupilName:'Efternamn, Anna'}};</script>"
)


if AVAILABLE:

    class FakeResponse:
        def __init__(self, status=200, text="", json_data=None, location=None, url=HUB):
            self.status = status
            self._text = text
            self._json = json_data
            self.headers = {"Location": location} if location else {}
            self.url = yarl.URL(url)

        async def text(self):
            return self._text

        async def json(self, content_type=None):  # noqa: ANN001
            return self._json

        def release(self):
            return None

        @property
        def ok(self):
            return self.status < 400

    class FakeCookieJar:
        def __init__(self):
            self.cleared = 0

        def clear(self):
            self.cleared += 1

        def filter_cookies(self, url):  # noqa: ANN001
            return {}

    class FakeSession:
        """Skriptad session: returnerar svar per (metod, url)."""

        def __init__(self, routes):
            self._routes = routes
            self.cookie_jar = FakeCookieJar()
            self.calls: list[tuple[str, str, object]] = []

        async def request(self, method, url, data=None, headers=None, allow_redirects=False, timeout=None):  # noqa: ANN001
            self.calls.append((method, str(url), data))
            for route_method, predicate, responder in self._routes:
                if route_method == method and predicate(str(url)):
                    return responder() if callable(responder) else responder
            raise AssertionError(f"oväntat anrop: {method} {url}")

        async def close(self):
            return None


@unittest.skipUnless(AVAILABLE, "kräver aiohttp och Home Assistant-paketet")
class TestLogin(unittest.IsolatedAsyncioTestCase):
    def _routes(self, hub_html=HUB_HTML):
        """Webbläsarens flöde: formulär → POST → hubbens oauth-sida → POST → LoginCallback."""
        state = {"posts": 0}

        def mentor_post():
            state["posts"] += 1
            if state["posts"] == 1:
                return FakeResponse(302, location=HUB + "authentication/authentication/login?apitype=im1")
            return FakeResponse(302, location=HUB + "Authentication/Authentication/LoginCallback")

        return [
            ("POST", lambda u: "isauthenticated" in u, FakeResponse(200, "{}")),
            ("POST", lambda u: u == MENTOR, mentor_post),
            ("GET", lambda u: u == MENTOR, FakeResponse(200, LOGIN_HTML)),
            ("GET", lambda u: "logincallback" in u.lower(), FakeResponse(302, location=HUB + "#/")),
            ("GET", lambda u: u.endswith("#/"), FakeResponse(200, "")),
            ("GET", lambda u: "authentication/authentication/login" in u.lower(), FakeResponse(200, OAUTH_HTML)),
            ("GET", lambda u: u == HUB, FakeResponse(200, hub_html)),
        ]

    def test_redirect_headers_keep_referer_like_a_browser(self):
        """Hubbens LoginCallback kraschar utan Referer – den ska följa med över redirects."""
        headers = {"Accept": "text/html", "Origin": HUB, "Referer": HUB, "Content-Type": "x"}
        same = InfomentorApi._redirect_headers(headers, HUB + "Authentication/LoginCallback")  # noqa: SLF001
        self.assertEqual(same, {"Accept": "text/html", "Referer": HUB})
        cross = InfomentorApi._redirect_headers(  # noqa: SLF001
            {"Referer": MENTOR}, HUB + "authentication/authentication/login"
        )
        self.assertEqual(cross, {"Referer": "https://infomentor.se/"})

    async def test_unauthorized_module_redirect_is_api_error_not_dead_session(self):
        """302 → HandleUnauthorizedRequest = saknar behörighet till modulen, inte utloggad."""
        session = FakeSession(
            [("POST", lambda u: True, FakeResponse(302, location=HUB + "Home/Home/HandleUnauthorizedRequest"))]
        )
        api = InfomentorApi(session, "a", "b")
        with self.assertRaises(ApiError):
            await api._post_hub("/task/task/GetTasks")  # noqa: SLF001
        self.assertEqual(len(session.calls), 1)  # ingen retry/omdirigering följdes

    async def test_login_returns_pupils(self):
        session = FakeSession(self._routes())
        api = InfomentorApi(session, "foralder@example.com", "hemligt")
        pupils = await api.async_login()
        self.assertEqual(len(pupils), 1)
        self.assertEqual(pupils[0]["name"], "Efternamn, Anna")
        # Skickades verkligen användarnamnet i något formulär?
        posted = [data for _, _, data in session.calls if isinstance(data, dict)]
        self.assertTrue(
            any(data.get("login_ascx$txtNotandanafn") == "foralder@example.com" for data in posted),
            "användarnamnet skickades aldrig",
        )
        self.assertTrue(
            any("__VIEWSTATE" in data for data in posted), "viewstate skickades aldrig"
        )

    async def test_login_without_session_marker_raises_invalid_auth(self):
        session = FakeSession(self._routes(hub_html="<html>ingen session</html>"))
        api = InfomentorApi(session, "foralder@example.com", "fel")
        with self.assertRaises(InvalidAuth):
            await api.async_login()

    async def test_non_json_endpoint_response_raises_api_error_not_auth(self):
        """Ett oväntat endpointsvar ska INTE tolkas som fel lösenord (issue #1)."""
        session = FakeSession([("POST", lambda u: True, FakeResponse(200, "<html>fel</html>"))])
        api = InfomentorApi(session, "a", "b")
        with self.assertRaises(ApiError):
            await api._post_hub("/task/task/GetTasks", {})  # noqa: SLF001

    async def test_error_status_raises_api_error(self):
        session = FakeSession([("POST", lambda u: True, FakeResponse(500, "server error"))])
        api = InfomentorApi(session, "a", "b")
        with self.assertRaises(ApiError):
            await api._post_hub("/task/task/GetTasks", {})  # noqa: SLF001

    async def test_redirect_is_logged_and_followed_once(self):
        """Omdirigeringen följs en gång; lyckas andra försöket returneras datan."""
        state = {"posts": 0}

        def post_route():
            state["posts"] += 1
            if state["posts"] == 1:
                return FakeResponse(302, location=HUB + "Authentication/Login")
            return FakeResponse(200, text=json.dumps({"items": []}))

        session = FakeSession(
            [
                ("POST", lambda u: True, post_route),
                ("GET", lambda u: True, FakeResponse(200, "<html></html>")),
            ]
        )
        api = InfomentorApi(session, "a", "b")
        assert await api._post_hub("/x", {}) == {"items": []}  # noqa: SLF001
        assert state["posts"] == 2

    async def test_redirect_twice_raises_invalid_auth(self):
        """Omdirigerar den även efter försöket ger vi upp med InvalidAuth."""
        session = FakeSession(
            [
                ("POST", lambda u: True, FakeResponse(302, location=HUB + "Authentication/Login")),
                ("GET", lambda u: True, FakeResponse(200, "<html></html>")),
            ]
        )
        api = InfomentorApi(session, "a", "b")
        with self.assertRaises(InvalidAuth):
            await api._post_hub("/timetable/timetable/appdata", {})  # noqa: SLF001

    async def test_empty_hub_body_raises_invalid_auth(self):
        class EmptySession(FakeSession):
            def __init__(self):
                super().__init__(
                    [("POST", lambda u: True, FakeResponse(200, ""))]
                )

        api = InfomentorApi(EmptySession(), "a", "b")
        with self.assertRaises(InvalidAuth):
            await api._post_hub("/timetable/timetable/gettimetablelist", {})  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
