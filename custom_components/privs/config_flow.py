"""Config flow: pair with the Privs app by approving a code there."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import PrivsClient, PrivsError
from .const import (
    CONF_DELEGATION_ID,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
    PAIRING_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class PairingExpiredError(Exception):
    """The code expired before anyone approved it."""


class PrivsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Signs in as a new delegate and waits for the app to approve its code."""

    VERSION = 1

    def __init__(self) -> None:
        """Start without a session."""
        self._client: PrivsClient | None = None
        self._install_name = ""
        self._code = ""
        self._pair_task: asyncio.Task[str] | None = None
        self._delegation_id = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Name this install as the app should show it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._install_name = user_input[CONF_NAME].strip() or self.hass.config.location_name
            if await self._start_pairing():
                return await self.async_step_pair()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default=self.hass.config.location_name): str}
            ),
            errors=errors,
        )

    async def _start_pairing(self) -> bool:
        try:
            if self._client is None:
                client = PrivsClient(async_get_clientsession(self.hass))
                await client.sign_in_anonymously()
                self._client = client
            self._code = await self._client.create_pairing(self._install_name)
        except PrivsError as err:
            _LOGGER.debug("Could not start pairing: %s", err)
            return False
        self._pair_task = None
        return True

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the code until the app approves it."""
        if self._pair_task is None:
            # Always show the code first, even if the answer is already in:
            # progress_done is only valid after a progress step.
            self._pair_task = self.hass.async_create_task(self._wait_for_approval())
            return self.async_show_progress(
                step_id="pair",
                progress_action="wait_for_approval",
                progress_task=self._pair_task,
                description_placeholders={"code": self._code},
            )
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="wait_for_approval",
                progress_task=self._pair_task,
                description_placeholders={"code": self._code},
            )
        try:
            self._delegation_id = self._pair_task.result()
        except PairingExpiredError:
            return self.async_show_progress_done(next_step_id="expired")
        except PrivsError as err:
            _LOGGER.debug("Pairing failed: %s", err)
            return self.async_show_progress_done(next_step_id="failed")
        return self.async_show_progress_done(next_step_id="finish")

    async def _wait_for_approval(self) -> str:
        assert self._client is not None
        while True:
            alive, delegation_id = await self._client.pairing_delegation_id(self._code)
            if delegation_id is not None:
                return delegation_id
            if not alive:
                raise PairingExpiredError
            await asyncio.sleep(PAIRING_POLL_INTERVAL)

    async def async_step_expired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a new code once the old one expired."""
        return await self._retry_step("expired", user_input)

    async def async_step_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer a new code after losing the connection while waiting."""
        return await self._retry_step("failed", user_input)

    async def _retry_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if await self._start_pairing():
                return await self.async_step_pair()
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id=step_id, errors=errors)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Save the session; the entry is named what the app called it."""
        assert self._client is not None
        try:
            delegation = await self._client.delegation(self._delegation_id)
        except PrivsError:
            delegation = None
        title = delegation.name if delegation else self._install_name
        data = {
            CONF_REFRESH_TOKEN: self._client.refresh_token,
            CONF_USER_ID: self._client.user_id,
            CONF_DELEGATION_ID: self._delegation_id,
        }
        await self.async_set_unique_id(self._delegation_id)
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), unique_id=self._delegation_id, title=title, data=data
            )
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=title, data=data)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The app disconnected this install, or its session was lost."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pair again with a new code."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            self._install_name = entry.title
            if await self._start_pairing():
                return await self.async_step_pair()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"name": entry.title},
            errors=errors,
        )
