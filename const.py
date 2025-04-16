"""Constants for the Zyxel NR7101 integration."""

DOMAIN = "zyxel_nr7101"
DEFAULT_NAME = "Zyxel NR7101"
DEFAULT_HOST = "https://192.168.1.1"
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 30

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# Sensor types
SENSOR_RSSI = "rssi"
SENSOR_SIGNAL_STRENGTH = "signal_strength"
SENSOR_CELL_ID = "cell_id"
SENSOR_CONNECTION_STATUS = "connection_status"
SENSOR_NETWORK_TYPE = "network_type"
SENSOR_FIRMWARE_VERSION = "firmware_version"
SENSOR_MODEL = "model"