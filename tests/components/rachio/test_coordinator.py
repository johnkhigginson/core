"""Tests for polling Rachio controllers."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant.common import async_fire_time_changed
from requests.exceptions import ConnectTimeout

from homeassistant.components.rachio.const import (
    SIGNAL_RACHIO_CONTROLLER_UPDATE,
    SIGNAL_RACHIO_RAIN_DELAY_UPDATE,
    SIGNAL_RACHIO_SCHEDULE_UPDATE,
    SIGNAL_RACHIO_ZONE_UPDATE,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

CONTROLLER_ID = "controller-0"
ZONE_ID = "zone-0"
SCHEDULE_ID = "schedule-0"

STANDBY_ENTITY_ID = "switch.controller_0_standby"
RAIN_DELAY_ENTITY_ID = "switch.controller_0_rain_delay"
ZONE_ENTITY_ID = "switch.controller_0_zone_0"
SCHEDULE_ENTITY_ID = "switch.controller_0_schedule_0_schedule"
ONLINE_ENTITY_ID = "binary_sensor.controller_0_connectivity"
CONTROLLER_ENTITY_IDS = [
    STANDBY_ENTITY_ID,
    RAIN_DELAY_ENTITY_ID,
    ZONE_ENTITY_ID,
    SCHEDULE_ENTITY_ID,
    ONLINE_ENTITY_ID,
]

ONE_CONTROLLER_INTERVAL = timedelta(minutes=10)


def _controller(index: int, **changes: Any) -> dict[str, Any]:
    """Return controller data as returned by person.get."""
    return {
        "id": f"controller-{index}",
        "name": f"Controller {index}",
        "serialNumber": f"serial-{index}",
        "macAddress": f"00:9D:6B:00:00:0{index}",
        "model": "GENERATION3",
        "status": "ONLINE",
        "on": True,
        "rainSensorTripped": False,
        "zones": [
            {
                "id": f"zone-{index}",
                "name": f"Zone {index}",
                "zoneNumber": 1,
                "enabled": True,
            }
        ],
        "scheduleRules": [
            {
                "id": f"schedule-{index}",
                "name": f"Schedule {index}",
                "enabled": True,
                "summary": "Every day at 6:00 AM",
                "totalDuration": 900,
            }
        ],
        "flexScheduleRules": [],
    } | changes


def _person(*devices: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a person.get response."""
    return (
        {"status": 200},
        {"username": "testuser", "id": "test-user-id", "devices": list(devices)},
    )


async def _tick(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    delta: timedelta = ONE_CONTROLLER_INTERVAL,
) -> None:
    """Advance time and run the scheduled updates."""
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize("mock_rachio", [[_controller(0)]], indirect=True)
@pytest.mark.usefixtures("freezer", "init_integration")
async def test_poll_updates_state(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a poll updates every controller entity."""
    assert hass.states.is_state(STANDBY_ENTITY_ID, STATE_OFF)
    assert hass.states.is_state(RAIN_DELAY_ENTITY_ID, STATE_OFF)
    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_OFF)
    assert hass.states.is_state(SCHEDULE_ENTITY_ID, STATE_OFF)
    assert hass.states.is_state(ONLINE_ENTITY_ID, STATE_ON)
    schedule = hass.states.get(SCHEDULE_ENTITY_ID)
    assert schedule.attributes["Enabled"] is True
    assert schedule.attributes["icon"] == "mdi:water"

    delay_end = dt_util.utcnow() + timedelta(minutes=15)
    disabled_schedule = _controller(0)["scheduleRules"][0] | {"enabled": False}
    mock_rachio.person.get.return_value = _person(
        _controller(
            0,
            status="OFFLINE",
            on=False,
            rainDelayExpirationDate=int(delay_end.timestamp() * 1000),
            scheduleRules=[disabled_schedule],
        ),
        # Controllers the integration did not set up are ignored
        _controller(9),
    )
    mock_rachio.device.current_schedule.return_value = (
        {"status": 200},
        {"zoneId": ZONE_ID, "scheduleId": SCHEDULE_ID},
    )

    await _tick(hass, freezer)

    assert hass.states.is_state(STANDBY_ENTITY_ID, STATE_ON)
    assert hass.states.is_state(RAIN_DELAY_ENTITY_ID, STATE_ON)
    assert hass.states.is_state(ZONE_ENTITY_ID, STATE_ON)
    assert hass.states.is_state(SCHEDULE_ENTITY_ID, STATE_ON)
    assert hass.states.is_state(ONLINE_ENTITY_ID, STATE_OFF)
    schedule = hass.states.get(SCHEDULE_ENTITY_ID)
    assert schedule.attributes["Enabled"] is False
    assert schedule.attributes["icon"] == "mdi:water-off"
    mock_rachio.device.current_schedule.assert_called_with(CONTROLLER_ID)

    # The polled rain delay ends on time, before the next poll
    mock_rachio.person.get.reset_mock()
    await _tick(hass, freezer, timedelta(minutes=5, seconds=1))

    assert hass.states.is_state(RAIN_DELAY_ENTITY_ID, STATE_OFF)
    mock_rachio.person.get.assert_not_called()


@pytest.mark.parametrize(
    ("mock_rachio", "interval", "calls"),
    [
        pytest.param([_controller(0)], timedelta(minutes=10), 2, id="1_controller"),
        pytest.param(
            [_controller(0), _controller(1), _controller(2)],
            timedelta(minutes=20),
            4,
            id="3_controllers",
        ),
        pytest.param(
            [_controller(index) for index in range(5)],
            timedelta(minutes=30),
            6,
            id="5_controllers",
        ),
    ],
    indirect=["mock_rachio"],
)
@pytest.mark.usefixtures("freezer", "init_integration")
async def test_api_calls_per_poll(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    interval: timedelta,
    calls: int,
) -> None:
    """Test one poll costs one call for the account plus one per controller."""
    mock_rachio.person.get.reset_mock()
    mock_rachio.device.current_schedule.reset_mock()

    await _tick(hass, freezer, interval - timedelta(seconds=5))

    mock_rachio.person.get.assert_not_called()
    mock_rachio.device.current_schedule.assert_not_called()

    await _tick(hass, freezer, timedelta(seconds=5))

    assert (
        mock_rachio.person.get.call_count
        + mock_rachio.device.current_schedule.call_count
        == calls
    )
    mock_rachio.person.get.assert_called_once_with("test-user-id")


@pytest.mark.parametrize("mock_rachio", [[_controller(0)]], indirect=True)
@pytest.mark.parametrize(
    ("method", "response", "side_effect"),
    [
        pytest.param("person.get", ({"status": 500}, {}), None, id="person_error"),
        pytest.param(
            "person.get", ({"status": 429}, {}), None, id="person_rate_limited"
        ),
        pytest.param("person.get", None, ConnectTimeout, id="person_timeout"),
        pytest.param(
            "device.current_schedule",
            ({"status": 500}, {}),
            None,
            id="current_schedule_error",
        ),
        pytest.param("person.get", _person(), None, id="controller_missing"),
    ],
)
@pytest.mark.usefixtures("freezer", "init_integration")
async def test_poll_failure_and_recovery(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    method: str,
    response: tuple[dict[str, Any], dict[str, Any]] | None,
    side_effect: type[Exception] | None,
) -> None:
    """Test a failed poll makes entities unavailable until the next good poll."""
    api_call = mock_rachio
    for name in method.split("."):
        api_call = getattr(api_call, name)
    good_response = api_call.return_value
    api_call.return_value = response
    api_call.side_effect = side_effect

    await _tick(hass, freezer)

    for entity_id in CONTROLLER_ENTITY_IDS:
        assert hass.states.is_state(entity_id, STATE_UNAVAILABLE)

    api_call.return_value = good_response
    api_call.side_effect = None
    mock_rachio.person.get.return_value = _person(_controller(0, on=False))

    await _tick(hass, freezer)

    for entity_id in CONTROLLER_ENTITY_IDS:
        assert not hass.states.is_state(entity_id, STATE_UNAVAILABLE)
    assert hass.states.is_state(STANDBY_ENTITY_ID, STATE_ON)


@pytest.mark.parametrize("mock_rachio", [[_controller(0)]], indirect=True)
@pytest.mark.parametrize(
    ("signal", "data", "entity_id", "state"),
    [
        pytest.param(
            SIGNAL_RACHIO_CONTROLLER_UPDATE,
            {"deviceId": CONTROLLER_ID, "subType": "SLEEP_MODE_ON"},
            STANDBY_ENTITY_ID,
            STATE_ON,
            id="standby",
        ),
        pytest.param(
            SIGNAL_RACHIO_RAIN_DELAY_UPDATE,
            {
                "deviceId": CONTROLLER_ID,
                "subType": "RAIN_DELAY_ON",
                "endTime": "2099-01-01T00:00:00Z",
            },
            RAIN_DELAY_ENTITY_ID,
            STATE_ON,
            id="rain_delay",
        ),
        pytest.param(
            SIGNAL_RACHIO_ZONE_UPDATE,
            {
                "deviceId": CONTROLLER_ID,
                "zoneId": ZONE_ID,
                "subType": "ZONE_STARTED",
                "summary": "Zone started",
            },
            ZONE_ENTITY_ID,
            STATE_ON,
            id="zone",
        ),
        pytest.param(
            SIGNAL_RACHIO_SCHEDULE_UPDATE,
            {
                "deviceId": CONTROLLER_ID,
                "scheduleId": SCHEDULE_ID,
                "subType": "SCHEDULE_STARTED",
            },
            SCHEDULE_ENTITY_ID,
            STATE_ON,
            id="schedule",
        ),
        pytest.param(
            SIGNAL_RACHIO_CONTROLLER_UPDATE,
            {"deviceId": CONTROLLER_ID, "subType": "OFFLINE"},
            ONLINE_ENTITY_ID,
            STATE_OFF,
            id="online",
        ),
    ],
)
@pytest.mark.usefixtures("freezer", "init_integration")
async def test_webhook_wins_over_stale_poll(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
    signal: str,
    data: dict[str, Any],
    entity_id: str,
    state: str,
) -> None:
    """Test a webhook applies at once and a poll right after it is ignored."""
    mock_rachio.person.get.reset_mock()
    mock_rachio.device.current_schedule.reset_mock()
    await _tick(hass, freezer, ONE_CONTROLLER_INTERVAL - timedelta(seconds=30))

    async_dispatcher_send(hass, signal, data)
    await hass.async_block_till_done()

    assert hass.states.is_state(entity_id, state)
    mock_rachio.person.get.assert_not_called()
    mock_rachio.device.current_schedule.assert_not_called()

    # This poll runs 30 seconds after the webhook and still has the old state
    await _tick(hass, freezer, timedelta(seconds=30))

    mock_rachio.person.get.assert_called_once()
    assert hass.states.is_state(entity_id, state)

    # Later polls are trusted again
    await _tick(hass, freezer)

    assert not hass.states.is_state(entity_id, state)


@pytest.mark.parametrize("mock_rachio", [[_controller(0)]], indirect=True)
@pytest.mark.usefixtures("freezer", "init_integration")
async def test_webhook_for_other_entity_does_not_hold_poll(
    hass: HomeAssistant,
    mock_rachio: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a webhook only holds back polled state of the entity it updated."""
    await _tick(hass, freezer, ONE_CONTROLLER_INTERVAL - timedelta(seconds=30))
    async_dispatcher_send(
        hass,
        SIGNAL_RACHIO_CONTROLLER_UPDATE,
        {"deviceId": CONTROLLER_ID, "subType": "OFFLINE"},
    )
    await hass.async_block_till_done()
    mock_rachio.person.get.return_value = _person(_controller(0, on=False))

    await _tick(hass, freezer, timedelta(seconds=30))

    assert hass.states.is_state(STANDBY_ENTITY_ID, STATE_ON)
    assert hass.states.is_state(ONLINE_ENTITY_ID, STATE_OFF)
