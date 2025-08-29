"""Config flow for Zyxel integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .const import DEFAULT_HOST, DEFAULT_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

try:
    from nr7101 import nr7101
    NR7101_AVAILABLE = True
    _LOGGER.debug("Successfully imported nr7101 library")
except ImportError as e:
    _LOGGER.error(f"Failed to import nr7101 library: {e}")
    NR7101_AVAILABLE = False
    nr7101 = None
except Exception as e:
    _LOGGER.error(f"Unexpected error importing nr7101 library: {e}")
    NR7101_AVAILABLE = False
    nr7101 = None

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def validate_input(hass: core.HomeAssistant, data):
    """Validate that the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """

    # Check if nr7101 library is available
    if not NR7101_AVAILABLE or nr7101 is None:
        _LOGGER.error("nr7101 library is not available - check requirements in manifest.json")
        raise Exception("Required nr7101 library is not installed or failed to import")

    try:
        _LOGGER.debug(f"Attempting connection to {data[CONF_HOST]} with username {data[CONF_USERNAME]}")

        # Create router instance and test connection
        try:
            _LOGGER.debug(f"Creating NR7101 instance with library: {nr7101}")
            router = await hass.async_add_executor_job(
                nr7101.NR7101,
                data[CONF_HOST],
                data[CONF_USERNAME],
                data[CONF_PASSWORD]
            )
            _LOGGER.debug(f"Successfully created router instance: {type(router)}")
        except Exception as init_ex:
            _LOGGER.error(f"Failed to initialize NR7101 router object: {init_ex}")
            _LOGGER.error("This might indicate a library version issue or network connectivity problem")
            _LOGGER.error(f"nr7101 module: {nr7101}")
            _LOGGER.error(f"Available attributes in nr7101: {dir(nr7101) if nr7101 else 'None'}")
            raise Exception(f"Router initialization failed: {init_ex}")

        # Check if encryption is required (with fallback for older library versions)
        encryption_required = getattr(router, 'encryption_required', False)
        rsa_key = getattr(router, 'rsa_key', None)
        sessionkey = getattr(router, 'sessionkey', None)

        _LOGGER.info(f"Router encryption: {'enabled' if encryption_required else 'disabled'}")

        # Test login first
        try:
            login_success = await hass.async_add_executor_job(router.login)
        except Exception as login_ex:
            _LOGGER.error(f"Login failed: {login_ex}")
            raise Exception(f"Login failed: {login_ex}")

        if not login_success:
            _LOGGER.error("Login failed - check credentials")
            raise Exception("Login failed - check credentials")

        _LOGGER.info("Login successful")

        # Probe available endpoints for debugging
        try:
            available_endpoints = await hass.async_add_executor_job(router.probe_available_endpoints)
            _LOGGER.info(f"Router has {len(available_endpoints)} available endpoints")
        except Exception as probe_ex:
            _LOGGER.warning(f"Failed to probe endpoints: {probe_ex}")
            available_endpoints = []

        # Test that we can get some data
        try:
            test_data = await hass.async_add_executor_job(router.get_status)
            if test_data:
                _LOGGER.info("Router data retrieved successfully")
            else:
                _LOGGER.warning("No data from router, but connection works")
                test_data = {"connection": "success"}
        except Exception as status_ex:
            _LOGGER.warning(f"Data retrieval failed: {status_ex}")
            test_data = {"login": "success"}

    except Exception as ex:
        _LOGGER.error("Unable to connect to Zyxel device: %s", ex)
        raise ConnectionError from ex

    # Return info that you want to store in the config entry.
    return {"title": f"Zyxel device: ({data[CONF_HOST]})"}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zyxel devices."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        success = False

        if user_input is not None:
            host = user_input[CONF_HOST]

            # sanitize entry
            if not host.startswith("http://") and not host.startswith("https://"):
                host = f"https://{host}"
                user_input[CONF_HOST] = host

            try:
                info = await validate_input(self.hass, user_input)
                success = True
            except Exception as e:  # pylint: disable=broad-except
                _LOGGER.exception("First attempt failed: %s", e)
                errors["base"] = "unknown"

            if not success and "https" not in user_input["host"]:
                _LOGGER.info("User specified http but it failed, trying https...")
                user_input["host"] = user_input["host"].replace("http://", "https://")
                try:
                    info = await validate_input(self.hass, user_input)
                    success = True
                except ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as e:  # pylint: disable=broad-except
                    _LOGGER.exception("Second attempt failed: %s", e)
                    errors["base"] = "unknown"

        if success:
            return self.async_create_entry(title=info["title"], data=user_input)
        else:
            return self.async_show_form(
                step_id="user", data_schema=DATA_SCHEMA, errors=errors
            )


class ConnectionError(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""
