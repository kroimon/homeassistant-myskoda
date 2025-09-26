"""Locks for the MySkoda integration."""

import logging
from datetime import timedelta

from aiohttp import ClientResponseError

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
)
from homeassistant.const import ATTR_CODE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import DiscoveryInfoType  # pyright: ignore [reportAttributeAccessIssue]
from homeassistant.util import Throttle

from myskoda.models.common import DoorLockedState
from myskoda.models.info import CapabilityId
from myskoda.mqtt import OperationFailedError

from .const import API_COOLDOWN_IN_SECONDS, CONF_SPIN_REGEX, COORDINATORS, DOMAIN
from .coordinator import MySkodaConfigEntry
from .entity import MySkodaEntity
from .utils import add_supported_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config: MySkodaConfigEntry,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the sensor platform."""
    add_supported_entities(
        available_entities=[
            DoorLock,
        ],
        coordinators=hass.data[DOMAIN][config.entry_id][COORDINATORS],
        async_add_entities=async_add_entities,
    )


class MySkodaLock(MySkodaEntity, LockEntity):
    """Base class for all locks in the MySkoda integration."""

    def __init__(self, coordinator, vin):
        super().__init__(coordinator, vin)


class DoorLock(MySkodaLock):
    """Central door lock."""

    entity_description = LockEntityDescription(
        key="door_lock",
        translation_key="door_lock",
    )

    _attr_code_format = CONF_SPIN_REGEX

    @property
    def is_locked(self) -> bool | None:
        if status := self.vehicle.status:
            return status.overall.doors_locked == DoorLockedState.LOCKED

    @Throttle(timedelta(seconds=API_COOLDOWN_IN_SECONDS))
    async def _async_lock_unlock(self, lock: bool, **kwargs):
        """Internal method to have a central location for the Throttle."""

        if self.is_locking or self.is_unlocking:
            return

        spin : str | None = kwargs.get(ATTR_CODE)
        myskoda, vin = self.coordinator.myskoda, self.vehicle.info.vin
        if lock:
            if self.is_locked:
                return
            
            self._attr_is_locking = True
            self.async_write_ha_state()

            try:
                await myskoda.lock(vin, spin)
                _LOGGER.info("Sent command to lock the vehicle.")
            except (OperationFailedError, TimeoutError, ClientResponseError) as exc:
                _LOGGER.error("Failed to lock vehicle: %s", exc)
                raise
            finally:
                self._attr_is_locking = False
                self.async_write_ha_state()

        else:
            if not self.is_locked:
                return

            self._attr_is_unlocking = True
            self.async_write_ha_state()

            try:
                await myskoda.unlock(vin, spin)
                _LOGGER.info("Sent command to unlock the vehicle.")
            except (OperationFailedError, TimeoutError, ClientResponseError) as exc:
                _LOGGER.error("Failed to unlock vehicle: %s", exc)
                raise
            finally:
                self._attr_is_unlocking = False
                self.async_write_ha_state()

    async def async_lock(self, **kwargs) -> None:
        await self._async_lock_unlock(lock=True, **kwargs)

    async def async_unlock(self, **kwargs) -> None:
        await self._async_lock_unlock(lock=False, **kwargs)

    def required_capabilities(self) -> list[CapabilityId]:
        return [CapabilityId.ACCESS]
