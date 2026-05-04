"""Shared overview configuration constants."""

CCUSAGE_TIMEZONE = "Asia/Shanghai"
CCUSAGE_WINDOW_DAYS = 7

LIVE_TOKEN_HOST = "127.0.0.1"
LIVE_TOKEN_PORT = 8765
LIVE_TOKEN_ENDPOINT = "http://{}:{}/token-usage".format(
    LIVE_TOKEN_HOST,
    LIVE_TOKEN_PORT,
)
LIVE_TOKEN_POLL_SECONDS = 300
LIVE_TOKEN_TIMEOUT_MS = 20000

