DOMAIN = "ha_zyxel"
DEFAULT_NAME = "Zyxel Device"
DEFAULT_HOST = "https://192.168.1.1"
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 30
# Faster than the default would only add load to a modem that already struggles.
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"