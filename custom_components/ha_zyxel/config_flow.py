"""Config flow for Zyxel integration."""
import logging

import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from nr7101 import nr7101

from .const import DEFAULT_HOST, DEFAULT_USERNAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

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
    try:
        _LOGGER.debug(f"Attempting connection to {data[CONF_HOST]} with username {data[CONF_USERNAME]}")

        # Create router instance and test connection
        router = await hass.async_add_executor_job(
            nr7101.NR7101,
            data[CONF_HOST],
            data[CONF_USERNAME],
            data[CONF_PASSWORD]
        )

        # Check if encryption is required
        _LOGGER.debug(f"Encryption required: {router.encryption_required}")
        if router.encryption_required:
            _LOGGER.debug(f"RSA key available: {'YES' if router.rsa_key else 'NO'}")
            if router.rsa_key:
                _LOGGER.debug(f"RSA key length: {len(router.rsa_key)}")

        # Test login first
        _LOGGER.debug("Starting login attempt...")
        login_success = await hass.async_add_executor_job(router.login)
        _LOGGER.debug(f"Login result: {login_success}")

        if not login_success:
            _LOGGER.error("Login failed - check credentials and device compatibility")
            raise Exception("Login failed - check credentials")

        # Probe available endpoints for debugging
        _LOGGER.debug("Probing available endpoints...")
        available_endpoints = await hass.async_add_executor_job(router.probe_available_endpoints)
        _LOGGER.info(f"Router has {len(available_endpoints)} available endpoints: {available_endpoints}")

        # Test that we can get some data (try multiple endpoints for different router types)
        test_data = await hass.async_add_executor_job(router.get_status)
        if not test_data:
            _LOGGER.debug("get_status returned no data, trying basic connection test")
            # Try basic connection test
            try:
                await hass.async_add_executor_job(router.connect)
                _LOGGER.info("Basic connection successful, but no status data available")
                # For routers without cellular data, we still consider this a success
                test_data = {"connection": "success"}
            except Exception as connect_ex:
                _LOGGER.error(f"Both get_status and connect failed: {connect_ex}")
                raise Exception("Connection/authentication failed.")

        _LOGGER.debug(f"Connection successful, got data: {type(test_data)} with keys: {list(test_data.keys()) if isinstance(test_data, dict) else 'not a dict'}")

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
