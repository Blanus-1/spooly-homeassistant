"""Konstanten für Spooly."""

from datetime import timedelta

DOMAIN = "spooly"

CONF_API_KEY = "api_key"
CONF_URL = "url"

DEFAULT_URL = "https://spooly.eu/api/v1"

UPDATE_INTERVAL = timedelta(minutes=5)

# Obergrenze der API pro Seite
PAGE_SIZE = 500
