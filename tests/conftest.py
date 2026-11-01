import pytest

from pytest_homeassistant_custom_component.common import mock_component


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def oauth_components(hass):
    # auth und my stehen im Manifest; hier gilt "my" als geladen, ohne das Frontend zu starten
    mock_component(hass, "auth")
    mock_component(hass, "my")
