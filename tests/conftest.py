"""Fixtures: HA-Testumgebung mit nachgebautem Gateway."""

import pytest

from .fake_gateway import FakeGateway, FakeSwModule

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations, hass):
    hass.config.language = "de"   # Entitätsnamen wie in der Zielinstallation
    yield


@pytest.fixture
async def gateway():
    gw = FakeGateway()
    gw.modules[3] = FakeSwModule()
    await gw.start()
    yield gw
    await gw.stop()
