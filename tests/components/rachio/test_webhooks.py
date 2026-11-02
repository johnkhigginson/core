"""Tests for the Rachio webhook health check."""

from datetime import timedelta
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed
from requests.exceptions import ConnectTimeout

from homeassistant.components.rachio.const import (
    WEBHOOK_CHECK_INTERVAL,
    WEBHOOK_CONST_ID,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant

from .test_switch import MOCK_CONTROLLER

OLD_WEBHOOK_ID = "test-device-webhook-id"
NEW_WEBHOOK_ID = "new-webhook-id"
MISSING_WARNING = "is missing, registering it again"

OWN_WEBHOOK = {"id": OLD_WEBHOOK_ID, "externalId": f"{WEBHOOK_CONST_ID}token"}
OTHER_WEBHOOK = {"id": "other-webhook-id", "externalId": "other-app"}

pytestmark = [
    pytest.mark.parametrize("mock_rachio", [[MOCK_CONTROLLER]], indirect=True),
    pytest.mark.parametrize("init_integration", [Platform.SWITCH], indirect=True),
    pytest.mark.usefixtures("init_integration"),
]


async def _tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time by one check interval."""
    freezer.tick(WEBHOOK_CHECK_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_webhook_present(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test nothing happens when our webhook is still registered."""
    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OTHER_WEBHOOK, OWN_WEBHOOK],
    )

    await _tick(hass, freezer)

    assert mock_rachio.notification.add.call_count == 1
    assert MISSING_WARNING not in caplog.text
    assert init_integration.state is ConfigEntryState.LOADED


async def test_webhook_missing_is_registered_again(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a missing webhook is registered again and the new id is tracked."""
    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OTHER_WEBHOOK],
    )
    mock_rachio.notification.add.return_value = (
        {"status": 200},
        {"id": NEW_WEBHOOK_ID},
    )

    await _tick(hass, freezer)

    assert MISSING_WARNING in caplog.text
    assert mock_rachio.notification.add.call_count == 2
    mock_rachio.notification.delete.assert_not_called()
    assert init_integration.state is ConfigEntryState.LOADED

    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OTHER_WEBHOOK, {"id": NEW_WEBHOOK_ID, "externalId": "x"}],
    )
    await _tick(hass, freezer)

    assert mock_rachio.notification.add.call_count == 2


@pytest.mark.parametrize(
    ("response", "side_effect"),
    [
        pytest.param(({"status": 500}, {"code": "1", "error": "bad"}), None, id="500"),
        pytest.param(({"status": 200}, {"code": "1", "error": "bad"}), None, id="dict"),
        pytest.param(None, ConnectTimeout, id="exception"),
    ],
)
async def test_webhook_check_api_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    response: tuple[dict, dict] | None,
    side_effect: type[Exception] | None,
) -> None:
    """Test an API error on the check does not register the webhook again."""
    mock_rachio.notification.get_device_webhook.reset_mock()
    mock_rachio.notification.get_device_webhook.return_value = response
    mock_rachio.notification.get_device_webhook.side_effect = side_effect

    await _tick(hass, freezer)

    assert mock_rachio.notification.get_device_webhook.call_count == 1
    assert mock_rachio.notification.add.call_count == 1
    assert MISSING_WARNING not in caplog.text
    assert init_integration.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("add_response", "add_side_effect"),
    [
        pytest.param(({"status": 500}, {"code": "1"}), None, id="error_response"),
        pytest.param(None, ConnectTimeout, id="exception"),
    ],
)
async def test_webhook_reregistration_failure(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    add_response: tuple[dict, dict] | None,
    add_side_effect: type[Exception] | None,
) -> None:
    """Test a failed re-registration is logged and the entry stays loaded."""
    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OTHER_WEBHOOK],
    )
    mock_rachio.notification.add.return_value = add_response
    mock_rachio.notification.add.side_effect = add_side_effect

    await _tick(hass, freezer)

    assert "Failed to register webhook" in caplog.text
    assert mock_rachio.notification.add.call_count == 2
    assert init_integration.state is ConfigEntryState.LOADED


async def test_webhook_check_stops_after_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test the health check no longer calls the API after unload."""
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    mock_rachio.notification.get_device_webhook.reset_mock()

    await _tick(hass, freezer)

    mock_rachio.notification.get_device_webhook.assert_not_called()


async def test_stop_listener_deletes_webhook_once(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
) -> None:
    """Test our webhook is deleted once on shutdown."""
    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OTHER_WEBHOOK, OWN_WEBHOOK],
    )

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_rachio.notification.delete.assert_called_once_with(OLD_WEBHOOK_ID)


async def test_stop_listener_removed_on_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
) -> None:
    """Test the stop listener is removed when the entry is unloaded."""
    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OWN_WEBHOOK],
    )
    await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    mock_rachio.notification.delete.reset_mock()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    mock_rachio.notification.delete.assert_not_called()


async def test_unload_deletes_webhook(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
) -> None:
    """Test our webhook is deleted when the entry is unloaded."""
    mock_rachio.notification.get_device_webhook.return_value = (
        {"status": 200},
        [OTHER_WEBHOOK, OWN_WEBHOOK],
    )
    mock_rachio.notification.delete.reset_mock()

    await hass.config_entries.async_unload(init_integration.entry_id)

    mock_rachio.notification.delete.assert_called_once_with(OLD_WEBHOOK_ID)
    assert init_integration.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("response", "side_effect"),
    [
        pytest.param(({"status": 500}, {"code": "1"}), None, id="error_response"),
        pytest.param(None, ConnectTimeout, id="exception"),
    ],
)
async def test_unload_succeeds_when_webhook_deletion_fails(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_rachio: MagicMock,
    caplog: pytest.LogCaptureFixture,
    response: tuple[dict, dict] | None,
    side_effect: type[Exception] | None,
) -> None:
    """Test a failed webhook deletion is logged and the unload still succeeds."""
    mock_rachio.notification.get_device_webhook.return_value = response
    mock_rachio.notification.get_device_webhook.side_effect = side_effect

    await hass.config_entries.async_unload(init_integration.entry_id)

    assert "Failed to delete webhook" in caplog.text
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_unload_after_stop_does_not_log_error(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test unloading after the stop event does not remove the fired listener."""
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    await hass.config_entries.async_unload(init_integration.entry_id)

    assert "Unable to remove unknown job listener" not in caplog.text
    assert init_integration.state is ConfigEntryState.NOT_LOADED
