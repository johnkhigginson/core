"""Integration with the Rachio Iro sprinkler system controller."""

from datetime import datetime
import logging
import secrets

from rachiopy import Rachio
from requests.exceptions import ConnectTimeout, RequestException

from homeassistant.components import cloud
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    CONF_API_KEY,
    CONF_WEBHOOK_ID,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CLOUDHOOK_URL,
    CONF_MANUAL_RUN_MINS,
    DOMAIN,
    WEBHOOK_CHECK_INTERVAL,
)
from .device import RachioConfigEntry, RachioPerson
from .services import async_setup_services
from .webhooks import (
    async_get_or_create_registered_webhook_id_and_url,
    async_register_webhook,
    async_unregister_webhook,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Rachio integration."""
    async_setup_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RachioConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
    async_unregister_webhook(hass, entry)
    await _async_delete_webhooks(hass, entry.runtime_data)


async def _async_delete_webhooks(hass: HomeAssistant, person: RachioPerson) -> None:
    """Delete our webhooks on Rachio, logging failures."""
    for controller in person.controllers:
        try:
            await hass.async_add_executor_job(controller.deinit_webhooks)
        except (RequestException, HomeAssistantError) as err:
            _LOGGER.warning("Failed to delete webhook for %s: %s", controller, err)


async def async_remove_entry(hass: HomeAssistant, entry: RachioConfigEntry) -> None:
    """Remove a rachio config entry."""
    if CONF_CLOUDHOOK_URL in entry.data:
        await cloud.async_delete_cloudhook(hass, entry.data[CONF_WEBHOOK_ID])


async def async_setup_entry(hass: HomeAssistant, entry: RachioConfigEntry) -> None:
    """Set up the Rachio config entry."""

    config = entry.data
    options = entry.options

    # CONF_MANUAL_RUN_MINS can only come from a yaml import
    if not options.get(CONF_MANUAL_RUN_MINS) and config.get(CONF_MANUAL_RUN_MINS):
        options_copy = options.copy()
        options_copy[CONF_MANUAL_RUN_MINS] = config[CONF_MANUAL_RUN_MINS]
        hass.config_entries.async_update_entry(entry, options=options_copy)

    # Configure API
    api_key = config[CONF_API_KEY]
    rachio = Rachio(api_key)

    # Get the URL of this server
    rachio.webhook_auth = secrets.token_hex()
    try:
        webhook_url = await async_get_or_create_registered_webhook_id_and_url(
            hass, entry
        )
    except cloud.CloudNotConnected as exc:
        # User has an active cloud subscription, but the connection to the cloud is down
        raise ConfigEntryNotReady from exc
    rachio.webhook_url = webhook_url

    person = RachioPerson(rachio, entry)

    # Get the API user
    try:
        await person.async_setup(hass)
    except ConfigEntryAuthFailed as error:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="authentication_failed",
        ) from error
    except ConnectTimeout as error:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        ) from error

    # Check for Rachio controller devices
    if not person.controllers and not person.base_stations:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="no_devices_found",
        )
    _LOGGER.debug(
        (
            "%d Rachio device(s) found; The url %s must be accessible from the internet"
            " in order to receive updates"
        ),
        len(person.controllers) + len(person.base_stations),
        webhook_url,
    )

    if person.controllers:
        await person.controller_coordinator.async_config_entry_first_refresh()
    for base in person.base_stations:
        await base.status_coordinator.async_config_entry_first_refresh()
        await base.schedule_coordinator.async_config_entry_first_refresh()

    # Enable platform
    entry.runtime_data = person
    async_register_webhook(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_check_webhooks(_now: datetime) -> None:
        if entry.state is not ConfigEntryState.LOADED:
            return
        for controller in person.controllers:
            try:
                registered = await hass.async_add_executor_job(
                    controller.webhook_registered
                )
            except RequestException as err:
                _LOGGER.debug("Could not check webhook for %s: %s", controller, err)
                continue
            if registered is not False:
                continue
            _LOGGER.warning(
                "Webhook for %s is missing, registering it again", controller
            )
            try:
                await hass.async_add_executor_job(controller.init_webhooks)
            except (RequestException, HomeAssistantError) as err:
                _LOGGER.warning(
                    "Failed to register webhook for %s: %s", controller, err
                )

    stop_fired = False

    async def _async_stop_delete_webhooks(_event: Event) -> None:
        nonlocal stop_fired
        stop_fired = True
        await _async_delete_webhooks(hass, person)

    remove_stop_listener = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP, _async_stop_delete_webhooks
    )

    @callback
    def _async_remove_stop_listener() -> None:
        if not stop_fired:
            remove_stop_listener()

    entry.async_on_unload(
        async_track_time_interval(hass, _async_check_webhooks, WEBHOOK_CHECK_INTERVAL)
    )
    entry.async_on_unload(_async_remove_stop_listener)
