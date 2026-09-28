"""Testkonfiguration för Home Assistant-integrationen."""

from __future__ import annotations

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001, PT004
    """Gör att custom_components laddas i testerna."""
    yield
