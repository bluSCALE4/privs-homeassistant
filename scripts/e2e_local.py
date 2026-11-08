"""End-to-end check against a local Supabase stack running the Privs schema.

Plays both sides with real HTTP and a real Realtime socket: a principal
(standing in for the app) and Home Assistant's delegate client.

Usage, from the Privs repo with `supabase start` running:
    .venv/bin/python scripts/e2e_local.py http://127.0.0.1:54321 <publishable key>
"""

import asyncio
from pathlib import Path
import sys
import uuid

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.privs.api import PrivsApiError, PrivsClient
from custom_components.privs.realtime import PrivsRealtime


def check(condition: bool, what: str) -> None:
    """Print and stop on the first failure."""
    print(("PASS " if condition else "FAIL ") + what)
    if not condition:
        sys.exit(1)


async def wait_for(predicate, seconds: float = 10) -> bool:
    """Poll until predicate() or the time runs out."""
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds
    while loop.time() < end:
        if predicate():
            return True
        await asyncio.sleep(0.2)
    return False


async def main(url: str, key: str) -> None:  # noqa: PLR0915 - one linear script
    async with aiohttp.ClientSession() as session:
        # The app's side: an anonymous principal who owns one person.
        async with session.post(f"{url}/auth/v1/signup", json={}, headers={"apikey": key}) as r:
            principal = await r.json()
        p_headers = {"apikey": key, "Authorization": f"Bearer {principal['access_token']}"}
        person_id = str(uuid.uuid4())
        async with session.post(
            f"{url}/rest/v1/persons",
            headers={**p_headers, "Prefer": "return=minimal"},
            json={
                "id": person_id,
                "owner_id": principal["user"]["id"],
                "name": "E2E Kid",
                "health": 3,
                "privilege": 4,
                "gender": "unspecified",
                "last_direction": "gaining",
                "settings": {},
                "theme": {},
            },
        ) as r:
            check(r.status == 201, f"principal creates a person ({r.status})")

        # Home Assistant's side.
        saved: list[str] = []
        ha = PrivsClient(session, None, saved.append, url=url, key=key)
        await ha.sign_in_anonymously()
        code = await ha.create_pairing("E2E Home")
        check(len(code) == 8, f"pairing code {code}")
        check(await ha.pairing_delegation_id(code) == (True, None), "pairing is pending")
        check(await ha.people() == [], "nothing visible before approval")

        async with session.post(
            f"{url}/rest/v1/rpc/approve_ha_pairing",
            headers=p_headers,
            json={
                "pairing_code": code,
                "delegation_name": "Home",
                "delegation_scope": "read_points",
                "person_ids": [person_id],
            },
        ) as r:
            check(r.status in (200, 204), f"principal approves ({r.status} {await r.text()})")

        alive, delegation_id = await ha.pairing_delegation_id(code)
        check(alive and delegation_id is not None, "HA sees the approval")
        delegation = await ha.delegation(delegation_id)
        check(delegation.scope == "read_points" and delegation.name == "Home", "delegation scope")

        changes = 0

        def changed() -> None:
            nonlocal changes
            changes += 1

        realtime = asyncio.create_task(PrivsRealtime(ha, session, changed).run())
        check(await wait_for(lambda: changes >= 1), "realtime channel joined")

        people = await ha.people()
        check(len(people) == 1 and people[0].privilege == 4, "ha_people shows the person")
        check(people[0].status is not None and people[0].in_session, f"status {people[0].status}")

        before = changes
        async with session.patch(
            f"{url}/rest/v1/persons",
            params={"id": f"eq.{person_id}"},
            headers=p_headers,
            json={"privilege": 5},
        ) as r:
            check(r.status in (200, 204), "principal changes the level in the app")
        check(await wait_for(lambda: changes > before), "realtime pushes the app's change")
        check((await ha.people())[0].privilege == 5, "HA reads the new level")

        await ha.apply_point_event(person_id, "lose", "From E2E")
        person = (await ha.people())[0]
        check(person.privilege == 4, "HA loses a point")

        async with session.get(
            f"{url}/rest/v1/status_snapshots",
            params={"person_id": f"eq.{person_id}", "select": "notes,delegation_id"},
            headers=p_headers,
        ) as r:
            snapshots = await r.json()
        check(
            any(
                s["notes"] == "From E2E" and s["delegation_id"] == delegation_id for s in snapshots
            ),
            "the app's history shows the note, attributed to the pairing",
        )

        refused = None
        for _ in range(12):
            try:
                await ha.apply_point_event(person_id, "gain")
            except PrivsApiError as err:
                refused = str(err)
                break
        check(refused == "rate limited", f"rate limit applies ({refused})")

        # Restart: a new client from the saved refresh token.
        check(saved and saved[-1] == ha.refresh_token, "refresh token handed back for saving")
        restarted = PrivsClient(session, ha.refresh_token, saved.append, url=url, key=key)
        check(len(await restarted.people()) == 1, "a restarted client resumes the session")

        async with session.delete(
            f"{url}/rest/v1/delegations", params={"id": f"eq.{delegation_id}"}, headers=p_headers
        ) as r:
            check(r.status in (200, 204), "principal disconnects in the app")
        check(await restarted.delegation(delegation_id) is None, "HA sees the disconnect")
        check(await restarted.people() == [], "and loses access")

        realtime.cancel()
        print("All checks passed")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
