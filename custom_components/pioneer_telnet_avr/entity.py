"""Shared receiver entity."""

from homeassistant.helpers.entity import Entity
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


class PioneerEntity(Entity):
    _attr_should_poll = False
    # Entity names are chosen per receiver during the config flow.
    _attr_has_entity_name = False

    def __init__(self, entry, receiver, suffix: str, name: str):
        self.entry = entry
        self.receiver = receiver
        self._attr_unique_id = f"{entry.unique_id}_{suffix}"
        if not receiver.options["showReceiverNameInSwitches"] and name.startswith(f"{entry.title} "):
            name = name[len(entry.title) + 1:]
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.unique_id)},
            "name": entry.title,
            "manufacturer": "Pioneer",
            "model": entry.title,
        }

    @property
    def available(self):
        return self.receiver.ready

    async def async_added_to_hass(self):
        # Recent HA versions compose the state friendly_name from the device
        # and entity even for legacy entities with has_entity_name=False.
        # A registry name is treated as the complete name. Set it only when
        # there is no user-provided name to preserve manual changes.
        if self.registry_entry is not None and self.registry_entry.name is None:
            er.async_get(self.hass).async_update_entity(self.entity_id, name=self.name)
        self.async_on_remove(self.receiver.subscribe(self.async_write_ha_state))
