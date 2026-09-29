"""Kalender för InfoMentor-schemat."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import InfomentorCoordinator
from .entity import InfomentorPupilEntity
from .util import parse_teachers


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Skapa en kalender per barn."""
    coordinator: InfomentorCoordinator = entry.runtime_data.coordinator
    if coordinator.data is not None:
        async_add_entities(
            InfoMentorCalendar(coordinator, entry, pupil)
            for pupil in coordinator.data.pupils
        )


class InfoMentorCalendar(InfomentorPupilEntity, CalendarEntity):
    """Veckoschema som kalender.

    Varje lektion blir en CalendarEvent. Raster (Lunch, Omb) är redan
    bortfiltrerade av normalize_lessons().
    """

    _attr_should_poll = False
    _attr_translation_key = "timetable"
    _attr_icon = "mdi:calendar-week"

    def __init__(self, coordinator, entry, pupil) -> None:
        super().__init__(coordinator, entry, pupil, "timetable")

    def _to_event(self, lesson: dict[str, Any]) -> CalendarEvent | None:
        start = dt_util.parse_datetime(lesson.get("start") or "")
        end = dt_util.parse_datetime(lesson.get("end") or "")
        if start is None or end is None:
            return None
        teachers = parse_teachers(lesson.get("teachers") or "")
        return CalendarEvent(
            start=start,
            end=end,
            summary=lesson.get("title") or "",
            description=", ".join(teachers) if teachers else "",
            location=lesson.get("room") or "",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Nästa kommande lektion."""
        pupil = self.pupil
        if pupil is None or not pupil.lessons:
            return None
        now = dt_util.now()
        for lesson in pupil.lessons:
            start = dt_util.parse_datetime(lesson.get("start") or "")
            if start and start >= now:
                return self._to_event(lesson)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Alla lektioner inom tidsintervallet."""
        pupil = self.pupil
        if pupil is None:
            return []
        events: list[CalendarEvent] = []
        for lesson in pupil.lessons:
            start = dt_util.parse_datetime(lesson.get("start") or "")
            if start is None:
                continue
            if start_date <= start <= end_date:
                event = self._to_event(lesson)
                if event is not None:
                    events.append(event)
        return events
