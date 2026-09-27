"""Optional number entities."""

from homeassistant.components.number import NumberEntity

from .extra_entities import setup_optional_entities


async def async_setup_entry(hass, entry, async_add_entities):
    for category in ['toneControls', 'channelLevels']:
        setup_optional_entities(hass, entry, async_add_entities, category, NumberEntity)
