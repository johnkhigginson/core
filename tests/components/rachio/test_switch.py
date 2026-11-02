"""Tests for the Rachio switch platform."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant.common import async_fire_time_changed

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

CONTROLLER_ID = "controller-id"
ZONE_ID = "zone-id"
ZONE_ENTITY_ID = "switch.test_controller_test_zone"
SCHEDULE_ID = "schedule-id"
SCHEDULE_ENTITY_ID = "switch.test_controller_test_schedule"
STANDBY_ENTITY_ID = "switch.test_controller_standby"
RAIN_DELAY_ENTITY_ID = "switch.test_controller_rain_delay"

MOCK_CONTROLLER = {
    "id": CONTROLLER_ID,
    "name": "Test Controller",
    "serialNumber": "serial-number",
    "macAddress": "00:9D:6B:00:00:01",
    "model": "GENERATION3",
    "on": True,
    "rainDelayExpirationDate": 0,
    "zones": [
        {
            "id": ZONE_ID,
            "name": "Test Zone",
            "zoneNumber": 1,
            "enabled": True,
        }
    ],
    "scheduleRules": [
        {
            "id": SCHEDULE_ID,
            "name": "Test",
            "totalDuration": 600,
            "enabled": True,
            "summary": "Test summary",
        }
    ],
    "flexScheduleRules": [],
}

pytestmark = [
    pytest.mark.parametrize("mock_rachio", [[MOCK_CONTROLLER]], indirect=True),
    pytest.mark.parametrize("init_integration", [Platform.SWITCH], indirect=True),
    pytest.mark.usefixtures("init_integration"),
]


@pytest.mark.parametrize(
    "success_status",
    [
        pytest.param(200, id="ok"),
        pytest.param(204, id="no-content"),
    ],
)
async def test_zone_services_update_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    success_status: int,
) -> None:
    """Test zone services optimistically update Home Assistant state."""
    mock_rachio.device.stop_water.return_value = ({"status": success_status}, {})
    mock_rachio.zone.start.return_value = ({"status": success_status}, {})

    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_OFF)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ZONE_ENTITY_ID},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_ON)
    mock_rachio.device.stop_water.assert_called_once_with(CONTROLLER_ID)
    mock_rachio.zone.start.assert_called_once_with(ZONE_ID, 600)

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: ZONE_ENTITY_ID},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_OFF)
    assert mock_rachio.device.stop_water.call_count == 2


@pytest.mark.parametrize(
    ("response", "side_effect", "expected_exception"),
    [
        pytest.param(
            ({"status": 500}, {}),
            None,
            HomeAssistantError,
            id="error-response",
        ),
        pytest.param(None, RuntimeError, RuntimeError, id="exception"),
    ],
)
async def test_zone_start_failure_does_not_update_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    response: tuple[dict[str, int], dict[str, Any]] | None,
    side_effect: type[Exception] | None,
    expected_exception: type[Exception],
) -> None:
    """Test a failed start command does not optimistically update state."""
    mock_rachio.zone.start.return_value = response
    mock_rachio.zone.start.side_effect = side_effect

    with pytest.raises(expected_exception):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: ZONE_ENTITY_ID},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_OFF)


@pytest.mark.parametrize(
    ("response", "side_effect", "expected_exception"),
    [
        pytest.param(
            ({"status": 500}, {}),
            None,
            HomeAssistantError,
            id="error-response",
        ),
        pytest.param(None, RuntimeError, RuntimeError, id="exception"),
    ],
)
async def test_zone_stop_failure_does_not_update_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    response: tuple[dict[str, int], dict[str, Any]] | None,
    side_effect: type[Exception] | None,
    expected_exception: type[Exception],
) -> None:
    """Test a failed stop command does not optimistically update state."""
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: ZONE_ENTITY_ID},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_ON)

    mock_rachio.device.stop_water.return_value = response
    mock_rachio.device.stop_water.side_effect = side_effect

    with pytest.raises(expected_exception):
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: ZONE_ENTITY_ID},
            blocking=True,
        )
    await hass.async_block_till_done()

    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_ON)


CONTROLLER_COMMANDS = [
    pytest.param(
        STANDBY_ENTITY_ID,
        lambda rachio: rachio.device.turn_off,
        lambda rachio: rachio.device.turn_on,
        id="standby",
    ),
    pytest.param(
        RAIN_DELAY_ENTITY_ID,
        lambda rachio: rachio.device.rain_delay,
        lambda rachio: rachio.device.rain_delay,
        id="rain-delay",
    ),
    pytest.param(
        SCHEDULE_ENTITY_ID,
        lambda rachio: rachio.schedulerule.start,
        lambda rachio: rachio.device.stop_water,
        id="schedule",
    ),
]

FAILURES = [
    pytest.param(({"status": 500}, {}), None, HomeAssistantError, id="error-response"),
    pytest.param(None, RuntimeError, RuntimeError, id="exception"),
]


@pytest.mark.parametrize(
    ("entity_id", "get_on_mock", "get_off_mock"), CONTROLLER_COMMANDS
)
@pytest.mark.parametrize(
    "success_status",
    [
        pytest.param(200, id="ok"),
        pytest.param(204, id="no-content"),
    ],
)
async def test_controller_switches_update_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    entity_id: str,
    get_on_mock: Callable[[MagicMock], MagicMock],
    get_off_mock: Callable[[MagicMock], MagicMock],
    success_status: int,
) -> None:
    """Test standby, rain delay and schedule switches update state after commands."""
    for mock in (get_on_mock(mock_rachio), get_off_mock(mock_rachio)):
        mock.return_value = ({"status": success_status}, {})

    assert hass.states.is_state(entity_id, STATE_OFF)

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert hass.states.is_state(entity_id, STATE_ON)
    get_on_mock(mock_rachio).assert_called_once()

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert hass.states.is_state(entity_id, STATE_OFF)
    get_off_mock(mock_rachio).assert_called()


@pytest.mark.parametrize(
    ("entity_id", "get_on_mock", "get_off_mock"), CONTROLLER_COMMANDS
)
@pytest.mark.parametrize(("response", "side_effect", "expected_exception"), FAILURES)
async def test_controller_switch_turn_on_failure_does_not_update_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    entity_id: str,
    get_on_mock: Callable[[MagicMock], MagicMock],
    get_off_mock: Callable[[MagicMock], MagicMock],
    response: tuple[dict[str, int], dict[str, Any]] | None,
    side_effect: type[Exception] | None,
    expected_exception: type[Exception],
) -> None:
    """Test a failed turn on command does not update state."""
    mock = get_on_mock(mock_rachio)
    mock.return_value = response
    mock.side_effect = side_effect

    with pytest.raises(expected_exception):
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )
    await hass.async_block_till_done()

    assert hass.states.is_state(entity_id, STATE_OFF)


@pytest.mark.parametrize(
    ("entity_id", "get_on_mock", "get_off_mock"), CONTROLLER_COMMANDS
)
@pytest.mark.parametrize(("response", "side_effect", "expected_exception"), FAILURES)
async def test_controller_switch_turn_off_failure_does_not_update_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    entity_id: str,
    get_on_mock: Callable[[MagicMock], MagicMock],
    get_off_mock: Callable[[MagicMock], MagicMock],
    response: tuple[dict[str, int], dict[str, Any]] | None,
    side_effect: type[Exception] | None,
    expected_exception: type[Exception],
) -> None:
    """Test a failed turn off command does not update state."""
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.is_state(entity_id, STATE_ON)

    mock = get_off_mock(mock_rachio)
    mock.return_value = response
    mock.side_effect = side_effect

    with pytest.raises(expected_exception):
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: entity_id}, blocking=True
        )
    await hass.async_block_till_done()

    assert hass.states.is_state(entity_id, STATE_ON)


async def test_rain_delay_expires(hass: HomeAssistant) -> None:
    """Test the rain delay switch turns off when the requested delay ends."""
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: RAIN_DELAY_ENTITY_ID},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.is_state(RAIN_DELAY_ENTITY_ID, STATE_ON)

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=24, minutes=1))
    await hass.async_block_till_done()

    assert hass.states.is_state(RAIN_DELAY_ENTITY_ID, STATE_OFF)
