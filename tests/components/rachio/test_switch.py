"""Tests for the Rachio switch platform."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from homeassistant.components.rachio.const import DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

CONTROLLER_ID = "controller-id"
ZONE_ID = "zone-id"
ZONE_ENTITY_ID = "switch.test_controller_test_zone"
SCHEDULE_ID = "schedule-id"

MOCK_CONTROLLER = {
    "id": CONTROLLER_ID,
    "name": "Test Controller",
    "serialNumber": "serial-number",
    "macAddress": "00:9D:6B:00:00:01",
    "model": "GENERATION3",
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
            "name": "Morning",
            "totalDuration": 600,
            "enabled": True,
            "summary": "Morning run",
        }
    ],
    "flexScheduleRules": [],
}

pytestmark = [
    pytest.mark.parametrize("mock_rachio", [[MOCK_CONTROLLER]], indirect=True),
    pytest.mark.parametrize("init_integration", [Platform.SWITCH], indirect=True),
]


@pytest.fixture
def old_style_entries(entity_registry: er.EntityRegistry) -> None:
    """Register switches as created before has_entity_name was used."""
    entity_registry.async_get_or_create(
        Platform.SWITCH,
        DOMAIN,
        f"{CONTROLLER_ID}-zone-{ZONE_ID}",
        suggested_object_id="old_zone",
        has_entity_name=False,
        original_name="Test Zone",
    )
    entity_registry.async_get_or_create(
        Platform.SWITCH,
        DOMAIN,
        f"{CONTROLLER_ID}-schedule-{SCHEDULE_ID}",
        suggested_object_id="old_schedule",
        has_entity_name=False,
        original_name="Morning Schedule",
    )


@pytest.mark.usefixtures("init_integration")
@pytest.mark.parametrize(
    ("unique_id", "entity_id", "friendly_name"),
    [
        pytest.param(
            f"{CONTROLLER_ID}-zone-{ZONE_ID}",
            ZONE_ENTITY_ID,
            "Test Controller Test Zone",
            id="zone",
        ),
        pytest.param(
            f"{CONTROLLER_ID}-schedule-{SCHEDULE_ID}",
            "switch.test_controller_morning_schedule",
            "Test Controller Morning schedule",
            id="schedule",
        ),
        pytest.param(
            f"{CONTROLLER_ID}-standby",
            "switch.test_controller_standby",
            "Test Controller Standby",
            id="standby",
        ),
        pytest.param(
            f"{CONTROLLER_ID}-delay",
            "switch.test_controller_rain_delay",
            "Test Controller Rain delay",
            id="rain-delay",
        ),
    ],
)
async def test_entity_names_and_unique_ids(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    unique_id: str,
    entity_id: str,
    friendly_name: str,
) -> None:
    """Test unique IDs are unchanged and names are prefixed by the device name."""
    assert entity_registry.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id) == (
        entity_id
    )
    assert entity_registry.async_get(entity_id).has_entity_name
    assert hass.states.get(entity_id).attributes[ATTR_FRIENDLY_NAME] == friendly_name


@pytest.mark.usefixtures("old_style_entries", "init_integration")
@pytest.mark.parametrize(
    ("unique_id", "entity_id", "friendly_name"),
    [
        pytest.param(
            f"{CONTROLLER_ID}-zone-{ZONE_ID}",
            "switch.old_zone",
            "Test Controller Test Zone",
            id="zone",
        ),
        pytest.param(
            f"{CONTROLLER_ID}-schedule-{SCHEDULE_ID}",
            "switch.old_schedule",
            "Test Controller Morning schedule",
            id="schedule",
        ),
    ],
)
async def test_existing_entities_keep_entity_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    unique_id: str,
    entity_id: str,
    friendly_name: str,
) -> None:
    """Test entities registered without has_entity_name keep their entity ID."""
    assert entity_registry.async_get_entity_id(Platform.SWITCH, DOMAIN, unique_id) == (
        entity_id
    )
    assert entity_registry.async_get(entity_id).has_entity_name
    assert hass.states.get(entity_id).attributes[ATTR_FRIENDLY_NAME] == friendly_name


@pytest.mark.usefixtures("init_integration")
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


@pytest.mark.usefixtures("init_integration")
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


@pytest.mark.usefixtures("init_integration")
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
