"""Grundentiteter och enhetsinformation."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_NAMES, DOMAIN, HUB_BASE, MANUFACTURER, MODEL
from .coordinator import InfomentorCoordinator, PupilData
from .util import parse_names_option


def resolve_display_name(entry: ConfigEntry, pupil: PupilData) -> str:
    """Smeknamn från options om det finns, annars 'Förnamn Efternamn'."""
    overrides = parse_names_option(entry.options.get(CONF_NAMES))
    return overrides.get(pupil.name) or pupil.display_name or pupil.name


class InfomentorPupilEntity(CoordinatorEntity[InfomentorCoordinator]):
    """Entitet kopplad till ett barns enhet."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: InfomentorCoordinator,
        entry: ConfigEntry,
        pupil: PupilData,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._pupil_id = pupil.pupil_id
        self._attr_unique_id = f"{entry.entry_id}_{pupil.pupil_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{pupil.pupil_id}")},
            name=resolve_display_name(entry, pupil),
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=HUB_BASE,
        )

    @property
    def pupil(self) -> PupilData | None:
        """Aktuell data för barnet."""
        data = self.coordinator.data
        if data is None:
            return None
        return next((p for p in data.pupils if p.pupil_id == self._pupil_id), None)

    @property
    def available(self) -> bool:
        return super().available and self.pupil is not None


class InfomentorHubEntity(CoordinatorEntity[InfomentorCoordinator]):
    """Entitet kopplad till integrationsnavet (t.ex. skolmat)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: InfomentorCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_hub_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="InfoMentor",
            manufacturer=MANUFACTURER,
            model=MODEL,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=HUB_BASE,
        )
