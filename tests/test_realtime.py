"""Realtime message handling."""

from unittest.mock import MagicMock

import pytest

from custom_components.privs.api import PrivsError
from custom_components.privs.realtime import PrivsRealtime


def make() -> tuple[PrivsRealtime, MagicMock]:
    """A listener with a recording callback."""
    changed = MagicMock()
    return PrivsRealtime(MagicMock(), MagicMock(), changed), changed


def test_change_triggers_refresh() -> None:
    """Every postgres change refreshes."""
    realtime, changed = make()
    realtime._handle({"event": "postgres_changes", "payload": {}}, "1")
    changed.assert_called_once()


def test_join_ok_refreshes_once() -> None:
    """A (re)join refreshes to catch up on anything missed."""
    realtime, changed = make()
    realtime._handle({"event": "phx_reply", "ref": "1", "payload": {"status": "ok"}}, "1")
    changed.assert_called_once()
    realtime._handle({"event": "phx_reply", "ref": "2", "payload": {"status": "ok"}}, "1")
    changed.assert_called_once()


@pytest.mark.parametrize(
    "message",
    [
        {"event": "phx_reply", "ref": "1", "payload": {"status": "error"}},
        {"event": "phx_error", "payload": {}},
        {"event": "phx_close", "payload": {}},
    ],
)
def test_errors_reconnect(message: dict) -> None:
    """Refused joins and channel errors end the connection so run() reconnects."""
    realtime, _ = make()
    with pytest.raises(PrivsError):
        realtime._handle(message, "1")
