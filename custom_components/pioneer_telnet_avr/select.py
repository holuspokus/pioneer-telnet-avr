"""Optional select entities."""

from homeassistant.components.select import SelectEntity

from .extra_entities import setup_optional_entities


async def async_setup_entry(hass, entry, async_add_entities):
    for category in ['phaseControl']:
        setup_optional_entities(hass, entry, async_add_entities, category, SelectEntity)
