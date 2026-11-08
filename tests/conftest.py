"""Shared fixtures."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.privs.api import Delegation, Person
from custom_components.privs.const import (
    CONF_DELEGATION_ID,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

DELEGATION_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
AVA_ID = "33333333-3333-3333-3333-333333333333"
BEN_ID = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


def make_person(**overrides) -> Person:
    """A person who is visible and changeable, with overrides."""
    values = {
        "id": AVA_ID,
        "name": "Ava",
        "in_session": True,
        "can_change": True,
        "next_change_at": None,
        "privilege": 4,
        "health": 3,
        "max_privilege": 5,
        "start_level": 4,
        "status": "green",
    }
    values.update(overrides)
    return Person(**values)


@pytest.fixture
def people() -> list[Person]:
    """Ava (owned) and Ben (shared, outside his session)."""
    return [
        make_person(),
        make_person(
            id=BEN_ID,
            name="Ben",
            in_session=False,
            can_change=False,
            next_change_at=datetime.now(UTC) + timedelta(hours=2),
            privilege=None,
            health=None,
            max_privilege=None,
            start_level=None,
            status=None,
        ),
    ]


@pytest.fixture
def scope() -> str:
    """The delegation's scope."""
    return "read_points"


@pytest.fixture
def client(people: list[Person], scope: str) -> Generator[AsyncMock]:
    """PrivsClient with every network call mocked."""
    with (
        patch("custom_components.privs.PrivsClient", autospec=True) as cls,
        patch("custom_components.privs.config_flow.PrivsClient", new=cls),
        patch("custom_components.privs.PrivsRealtime.run", new=AsyncMock()),
    ):
        instance = cls.return_value
        instance.refresh_token = "refresh-1"
        instance.user_id = USER_ID
        instance.sign_in_anonymously.return_value = USER_ID
        instance.create_pairing.return_value = "ABCD2345"
        instance.pairing_delegation_id.return_value = (True, DELEGATION_ID)
        instance.delegation.return_value = Delegation(
            id=DELEGATION_ID, name="Home", scope=scope, active=True
        )
        instance.people.return_value = people
        yield instance


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip setting up the entry a flow creates."""
    with patch("custom_components.privs.async_setup_entry", return_value=True) as mock:
        yield mock


@pytest.fixture
def entry() -> MockConfigEntry:
    """A paired install."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        unique_id=DELEGATION_ID,
        data={
            CONF_REFRESH_TOKEN: "refresh-0",
            CONF_USER_ID: USER_ID,
            CONF_DELEGATION_ID: DELEGATION_ID,
        },
    )
