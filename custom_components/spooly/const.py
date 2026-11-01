"""Konstanten für Spooly."""

from datetime import timedelta

DOMAIN = "spooly"

CONF_API_KEY = "api_key"
CONF_URL = "url"

DEFAULT_URL = "https://spooly.eu/api/v1"

# feste Kennung der Integration in Spooly, ohne Geheimnis (öffentlicher Client mit PKCE)
OAUTH_CLIENT_ID = "spooly-home-assistant"
OAUTH_SCOPE = "spooly.read spooly.write"

UPDATE_INTERVAL = timedelta(minutes=5)

# Obergrenze der API pro Seite
PAGE_SIZE = 500
