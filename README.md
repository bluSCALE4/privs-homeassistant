# Privileges for Home Assistant

See the people you track in [Privs](https://github.com/bluSCALE4/Privs) in Home Assistant, and give or take points from automations, dashboards and voice.

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=bluSCALE4&repository=privs-homeassistant&category=integration)

Requires Home Assistant 2026.11 or later.

## Setup

1. Install **Privileges** from the Marketplace with the button above, or from the Privs app under **Settings > Home Assistant > Add to Home Assistant**. Restart Home Assistant.
2. Go to **Settings > Devices & services > Add integration > Privileges**, name this install and submit.
3. Home Assistant shows an 8-character code. On your iPhone, open Privs, go to **Settings > Home Assistant**, enter the code and tap **Continue**.
4. Choose who Home Assistant can see and whether it can change points, then tap **Connect**. Setup finishes on its own.

To disconnect, swipe the install away in Privs under **Settings > Home Assistant**. Home Assistant loses access right away and asks to pair again.

## What you get

Each person becomes a device with:

| Entity | What it shows |
|---|---|
| Status | Green, yellow or red |
| Level | Their privilege level |
| Hearts | Hearts left today (3 to 0) |
| In session | Whether Home Assistant may see them now |
| Next session change | When that changes next |
| Top level, Start level | Their settings (diagnostic) |
| Gain point, Lose point, Straight to bottom | Buttons, only when the pairing can change points |

For people shared with you, Home Assistant only sees what you can see: their state is unavailable outside your session times, and points can change only when you could change them yourself.

### Changing points with a note

The `privs.change_points` action gains or loses a point, or sends someone straight to the bottom, with an optional note that shows in their history in Privs:

```yaml
action: privs.change_points
data:
  device_id: 0123456789abcdef0123456789abcdef
  kind: lose
  note: Left the gate open
```

Home Assistant can make at most 10 changes a minute per pairing.

## How it works

Home Assistant signs in to the Privs backend as its own anonymous account. Approving its code in the app makes that account a delegate of yours, limited to the people and access you chose. No password or token from your phone is shared with Home Assistant. Changes arrive within seconds over a live connection, with a slower poll as a fallback.

<!-- ha-marketplace:exclude-start -->
## Development

```sh
uv venv -p 3.14 .venv
uv pip install -p .venv -r requirements_test.txt ruff
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

`scripts/e2e_local.py` runs the pairing, Realtime and point flow against a local Supabase stack from the Privs repo (`supabase start`):

```sh
.venv/bin/python scripts/e2e_local.py http://127.0.0.1:54321 <publishable key from supabase status>
```

Releases: bump `version` in `custom_components/privs/manifest.json`, then publish a GitHub release tagged with exactly that version (for example `0.1.0`).
<!-- ha-marketplace:exclude-end -->
