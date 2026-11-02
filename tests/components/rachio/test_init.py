"""Tests for the Rachio integration setup."""

from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant

from .test_switch import MOCK_CONTROLLER


async def _setup_entry(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the config entry with the webhook helpers patched."""
    config_entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.rachio.async_get_or_create_registered_webhook_id_and_url",
            return_value="http://example.com/webhook",
        ),
        patch("homeassistant.components.rachio.async_register_webhook"),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_authentication_failed(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_rachio: MagicMock,
) -> None:
    """Test setup fails and starts reauth when the API key is rejected."""
    mock_rachio.person.info.return_value = ({"status": 401}, {})

    await _setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.reason == "Authentication with the Rachio API failed"
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH
    assert flows[0]["context"]["entry_id"] == mock_config_entry.entry_id


async def test_setup_no_devices_found(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_rachio: MagicMock,
) -> None:
    """Test setup fails when the account has no devices."""
    mock_rachio.valve.list_base_stations.return_value = (
        {"status": 200},
        {"baseStations": []},
    )

    await _setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_config_entry.reason == "No Rachio devices found in the account"


@pytest.mark.parametrize("mock_rachio", [[MOCK_CONTROLLER]], indirect=True)
@pytest.mark.parametrize(
    "mock_call",
    [
        pytest.param("get_webhook_event_type", id="event_types"),
        pytest.param("add", id="add"),
    ],
)
async def test_setup_retry_on_webhook_registration_error(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_rachio: MagicMock,
    mock_call: str,
) -> None:
    """Test setup is retried when registering the webhook fails."""
    getattr(mock_rachio.notification, mock_call).return_value = (
        {"status": 500},
        {"code": "1"},
    )

    await _setup_entry(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
