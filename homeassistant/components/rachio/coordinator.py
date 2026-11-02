"""Coordinator object for the Rachio integration."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
import logging
from operator import itemgetter
from typing import TYPE_CHECKING, Any, override

from rachiopy import Rachio
from requests.exceptions import RequestException, Timeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    KEY_DAY_VIEWS,
    KEY_DEVICES,
    KEY_ID,
    KEY_PROGRAM_RUN_SUMMARIES,
    KEY_START_TIME,
    KEY_STATUS,
    KEY_VALVES,
)

if TYPE_CHECKING:
    from .device import RachioConfigEntry

_LOGGER = logging.getLogger(__name__)

DAY = "day"
MONTH = "month"
YEAR = "year"

UPDATE_DELAY_TIME = 8


class RachioUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator Class for Rachio Hose Timers."""

    def __init__(
        self,
        hass: HomeAssistant,
        rachio: Rachio,
        config_entry: RachioConfigEntry,
        base_station,
        base_count: int,
    ) -> None:
        """Initialize the Rachio Update Coordinator."""
        self.rachio = rachio
        self.base_station = base_station
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} update coordinator",
            # To avoid exceeding the rate limit, increase polling interval for
            # each additional base station on the account
            update_interval=timedelta(minutes=(base_count + 1)),
            # Debouncer used because the API takes a bit to update state changes
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=UPDATE_DELAY_TIME, immediate=False
            ),
        )

    @override
    async def _async_update_data(self) -> dict[str, Any]:
        """Update smart hose timer data."""
        try:
            data = await self.hass.async_add_executor_job(
                self.rachio.valve.list_valves, self.base_station[KEY_ID]
            )
        except Timeout as err:
            raise UpdateFailed(f"Could not connect to the Rachio API: {err}") from err
        return {valve[KEY_ID]: valve for valve in data[1][KEY_VALVES]}


class RachioScheduleUpdateCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Coordinator for fetching hose timer schedules."""

    def __init__(
        self,
        hass: HomeAssistant,
        rachio: Rachio,
        config_entry: RachioConfigEntry,
        base_station,
    ) -> None:
        """Initialize a Rachio schedule coordinator."""
        self.rachio = rachio
        self.base_station = base_station
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} schedule update coordinator",
            update_interval=timedelta(minutes=30),
        )

    @override
    async def _async_update_data(self) -> list[dict[str, Any]]:
        """Retrieve data for the past week and the next 60 days."""
        _now: datetime = dt_util.now()
        _time_start = _now - timedelta(days=7)
        _time_end = _now + timedelta(days=60)
        start: dict[str, int] = {
            YEAR: _time_start.year,
            MONTH: _time_start.month,
            DAY: _time_start.day,
        }
        end: dict[str, int] = {
            YEAR: _time_end.year,
            MONTH: _time_end.month,
            DAY: _time_end.day,
        }

        try:
            schedule = await self.hass.async_add_executor_job(
                self.rachio.summary.get_valve_day_views,
                self.base_station[KEY_ID],
                start,
                end,
            )
        except Timeout as err:
            raise UpdateFailed(f"Could not connect to the Rachio API: {err}") from err
        events = []
        # Flatten and sort dates
        for event in schedule[1][KEY_DAY_VIEWS]:
            events.extend(event[KEY_PROGRAM_RUN_SUMMARIES])
        return sorted(events, key=itemgetter(KEY_START_TIME))


@dataclass(frozen=True)
class RachioControllerData:
    """Polled state of a Rachio controller."""

    device: dict[str, Any]
    current_schedule: dict[str, Any]


class RachioControllerUpdateCoordinator(
    DataUpdateCoordinator[dict[str, RachioControllerData]]
):
    """Coordinator polling all Rachio controllers of an account.

    Webhooks remain the primary source of updates, this catches missed events.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        rachio: Rachio,
        config_entry: RachioConfigEntry,
        person_id: str,
        controller_ids: list[str],
    ) -> None:
        """Initialize the Rachio controller coordinator."""
        self.rachio = rachio
        self.person_id = person_id
        self.controller_ids = controller_ids
        self.poll_started = dt_util.utcnow()
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} controller update coordinator",
            # Each poll costs one call for the account plus one per controller,
            # so this keeps polling at 288 calls a day for any controller count
            update_interval=timedelta(minutes=5 * (len(controller_ids) + 1)),
        )

    @override
    async def _async_update_data(self) -> dict[str, RachioControllerData]:
        """Fetch the state of every controller."""
        self.poll_started = dt_util.utcnow()
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except RequestException as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
            ) from err

    def _fetch(self) -> dict[str, RachioControllerData]:
        """Fetch the controllers in one call and their running schedules."""
        person = self.rachio.person.get(self.person_id)
        _raise_for_update_status(person)
        data: dict[str, RachioControllerData] = {}
        for device in person[1][KEY_DEVICES]:
            if device[KEY_ID] not in self.controller_ids:
                continue
            current_schedule = self.rachio.device.current_schedule(device[KEY_ID])
            _raise_for_update_status(current_schedule)
            data[device[KEY_ID]] = RachioControllerData(device, current_schedule[1])
        return data


def _raise_for_update_status(response: tuple[dict[str, Any], Any]) -> None:
    """Raise UpdateFailed if a Rachio API response was not successful."""
    if int(response[0][KEY_STATUS]) != HTTPStatus.OK:
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="api_error",
            translation_placeholders={"error": str(response[1])},
        )
