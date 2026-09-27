"""Optional tuner controls."""

from homeassistant.components.button import ButtonEntity

from .extra_entities import setup_optional_entities


async def async_setup_entry(hass, entry, async_add_entities):
    for category in ['tunerControl']:
        setup_optional_entities(hass, entry, async_add_entities, category, ButtonEntity)
