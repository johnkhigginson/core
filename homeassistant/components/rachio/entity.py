"""Adapter to wrap the rachiopy api for home assistant."""

from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Any, override

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_NAME,
    DOMAIN,
    KEY_BATTERY_STATUS,
    KEY_CONNECTED,
    KEY_CURRENT_STATUS,
    KEY_FLOW_DETECTED,
    KEY_ID,
    KEY_LOW,
    KEY_NAME,
    KEY_REPLACE,
    KEY_REPORTED_STATE,
    KEY_STATE,
)
from .coordinator import (
    RachioControllerData,
    RachioControllerUpdateCoordinator,
    RachioUpdateCoordinator,
)
from .device import RachioIro

# The API can lag behind webhooks, so polls started this soon after a pushed
# state are not trusted for that entity
PUSH_HOLD_TIME = timedelta(minutes=1)


class RachioDevice(CoordinatorEntity[RachioControllerUpdateCoordinator]):
    """Base class for rachio devices."""

    def __init__(
        self, controller: RachioIro, coordinator: RachioControllerUpdateCoordinator
    ) -> None:
        """Initialize a Rachio device."""
        super().__init__(coordinator)
        self._controller = controller
        self._last_pushed: datetime | None = None
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    self._controller.serial_number,
                )
            },
            connections={
                (
                    dr.CONNECTION_NETWORK_MAC,
                    self._controller.mac_address,
                )
            },
            name=self._controller.name,
            model=self._controller.model,
            manufacturer=DEFAULT_NAME,
            configuration_url="https://app.rach.io",
        )

    @property
    @override
    def available(self) -> bool:
        """Return if the controller was in the last poll."""
        return (
            super().available
            and self._controller.controller_id in self.coordinator.data
        )

    @property
    def controller_data(self) -> RachioControllerData:
        """Return the polled controller state."""
        return self.coordinator.data[self._controller.controller_id]

    def _mark_pushed(self) -> None:
        """Note that the state was just set by a webhook or a command."""
        self._last_pushed = dt_util.utcnow()

    @abstractmethod
    def _update_attr(self) -> None:
        """Update the state from the polled controller state."""

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Apply polled state unless it may predate a pushed state."""
        if self.available and (
            self._last_pushed is None
            or self.coordinator.poll_started >= self._last_pushed + PUSH_HOLD_TIME
        ):
            self._update_attr()
        super()._handle_coordinator_update()

    @override
    async def async_added_to_hass(self) -> None:
        """Set the initial state from the polled controller state."""
        await super().async_added_to_hass()
        if self.available:
            self._update_attr()


class RachioHoseTimerEntity(CoordinatorEntity[RachioUpdateCoordinator]):
    """Base class for smart hose timer entities."""

    _attr_has_entity_name = True

    def __init__(
        self, data: dict[str, Any], coordinator: RachioUpdateCoordinator
    ) -> None:
        """Initialize a Rachio smart hose timer entity."""
        super().__init__(coordinator)
        self.id = data[KEY_ID]
        self._name = data[KEY_NAME]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.id)},
            model="Smart Hose Timer",
            name=self._name,
            manufacturer=DEFAULT_NAME,
            configuration_url="https://app.rach.io",
        )

    @property
    def reported_state(self) -> dict[str, Any]:
        """Return the reported state."""
        return self.coordinator.data[self.id][KEY_STATE][KEY_REPORTED_STATE]

    @property
    @override
    def available(self) -> bool:
        """Return if the entity is available."""
        return super().available and self.reported_state[KEY_CONNECTED]

    @property
    def battery(self) -> bool:
        """Return the battery status."""
        return self.reported_state[KEY_BATTERY_STATUS] in [KEY_LOW, KEY_REPLACE]

    @property
    def no_flow_detected(self) -> bool:
        """Return true if valve is on and flow is not detected."""
        if status := self.reported_state.get(KEY_CURRENT_STATUS):
            # Since this is a problem indicator we need the opposite of the API state
            return not status.get(KEY_FLOW_DETECTED, True)
        return False

    @abstractmethod
    def _update_attr(self) -> None:
        """Update the state and attributes."""

    @callback
    @override
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_attr()
        super()._handle_coordinator_update()
