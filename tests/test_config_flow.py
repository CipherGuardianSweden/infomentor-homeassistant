"""Tester för config- och optionsflödet (körs i CI)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant import config_entries, data_entry_flow  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402

from custom_components.infomentor.api import CannotConnect, InvalidAuth  # noqa: E402
from custom_components.infomentor.const import DOMAIN  # noqa: E402

USER_INPUT = {"username": "foralder@example.com", "password": "hemligt"}  # noqa: S105
VALIDATE = "custom_components.infomentor.config_flow._validate_credentials"


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """Ett lyckat inloggningsförsök skapar en config-post."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    with patch(VALIDATE, AsyncMock(return_value=[{"id": "1"}, {"id": "2"}])):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"]["username"] == USER_INPUT["username"]
    assert result["result"].unique_id == USER_INPUT["username"]


async def test_user_flow_invalid_auth(hass: HomeAssistant) -> None:
    """Fel uppgifter visar ett fel och skapar ingen post."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, AsyncMock(side_effect=InvalidAuth("nope"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Nätverksfel visas som cannot_connect."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(VALIDATE, AsyncMock(side_effect=CannotConnect("nere"))):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["errors"]["base"] == "cannot_connect"


async def test_already_configured(hass: HomeAssistant) -> None:
    """Samma konto kan inte läggas till två gånger."""
    with patch(VALIDATE, AsyncMock(return_value=[{"id": "1"}])):
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        first = await hass.config_entries.flow.async_configure(first["flow_id"], USER_INPUT)
        assert first["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY

        second = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        second = await hass.config_entries.flow.async_configure(second["flow_id"], USER_INPUT)

    assert second["type"] == data_entry_flow.FlowResultType.ABORT
    assert second["reason"] == "already_configured"
