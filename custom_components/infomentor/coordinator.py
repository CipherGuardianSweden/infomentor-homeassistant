"""DataUpdateCoordinator: loggar in och hämtar allt för alla barn."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, CannotConnect, InfomentorApi, InfomentorError, InvalidAuth
from .const import (
    CONF_ENABLE_LUNCH,
    CONF_MATEO_UNIT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    clamp_interval,
)
from .util import (
    display_name,
    normalize_attendance,
    normalize_calendar,
    normalize_lessons,
    normalize_notifications,
    normalize_tasks,
    parse_mateo_days,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PupilData:
    """Allt vi vet om ett barn vid en hämtning."""

    pupil_id: str
    name: str
    switch_url: str
    display_name: str
    lessons: list[dict[str, Any]] = field(default_factory=list)
    calendar: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    attendance: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InfomentorData:
    """Koordinatorns data."""

    pupils: list[PupilData] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    news: list[dict[str, Any]] = field(default_factory=list)
    lunch: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    lunch_unit: str | None = None


class InfomentorCoordinator(DataUpdateCoordinator[InfomentorData]):
    """Hämtar data med valt intervall."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, session: aiohttp.ClientSession
    ) -> None:
        self.entry = entry
        self.api = InfomentorApi(session, entry.data["username"], entry.data["password"])
        minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=clamp_interval(minutes)),
            config_entry=entry,
        )

    async def _async_update_data(self) -> InfomentorData:
        try:
            pupils = await self.api.async_login()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CannotConnect as err:
            raise UpdateFailed(f"Kunde inte nå InfoMentor: {err}") from err

        data = InfomentorData()
        child_by_id: dict[str, str] = {str(p.get("id")): str(p.get("name")) for p in pupils}

        for pupil in pupils:
            pupil_id = str(pupil.get("id"))
            who = display_name(str(pupil.get("name") or ""))

            # Själva barnbytet måste lyckas — annars blir allt fel.
            try:
                await self.api.async_switch_pupil(pupil)
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except CannotConnect as err:
                raise UpdateFailed(f"Kunde inte nå InfoMentor: {err}") from err
            except InfomentorError as err:
                raise UpdateFailed(f"Kunde inte byta till {who}: {err}") from err

            # Enskilda endpoints får fallera utan att fälla hela uppdateringen
            # (t.ex. en kommun där en endpoint svarar oväntat).
            lessons = normalize_lessons(
                await self._safe(f"schema ({who})", self.api.async_lessons(pupil), [])
            )
            calendar = normalize_calendar(
                await self._safe(f"kalender ({who})", self.api.async_calendar(pupil), [])
            )
            tasks = normalize_tasks(
                await self._safe(f"uppgifter ({who})", self.api.async_tasks(pupil), [])
            )
            attendance = normalize_attendance(
                await self._safe(f"närvaro ({who})", self.api.async_attendance(pupil), {})
            )

            data.pupils.append(
                PupilData(
                    pupil_id=pupil_id,
                    name=str(pupil.get("name") or ""),
                    switch_url=str(pupil.get("switchPupilUrl") or ""),
                    display_name=who,
                    lessons=lessons,
                    calendar=calendar,
                    tasks=tasks,
                    attendance=attendance,
                )
            )

        notifications = await self._safe("notiser", self.api.async_notifications(), [])
        data.notifications = normalize_notifications(notifications, child_by_id)
        data.news = await self._safe("nyheter", self.api.async_news(), [])

        if self.entry.options.get(CONF_ENABLE_LUNCH) and (
            unit := self.entry.options.get(CONF_MATEO_UNIT)
        ):
            days = await self._safe("skolmat", self.api.async_lunch(str(unit)), [])
            data.lunch = parse_mateo_days(days)
            data.lunch_unit = str(unit)

        return data

    async def _safe(self, label: str, awaitable, default):
        """Hämtar en endpoint; loggar och hoppar över vid endpoint-fel.

        InvalidAuth (död session) och CannotConnect (nere) får bubbla upp — de
        ska ge reauth respektive nytt försök. Ett oväntat endpointsvar ska inte.
        """
        try:
            return await awaitable
        except (InvalidAuth, CannotConnect):
            raise
        except (ApiError, InfomentorError) as err:
            _LOGGER.warning("InfoMentor: kunde inte hämta %s – hoppar över (%s)", label, err)
            return default
