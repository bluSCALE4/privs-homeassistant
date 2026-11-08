"""Constants for the Privileges integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "privs"

# The Privs Supabase project. The publishable key is the same one the iOS
# app ships with; every read and write is still limited by row level
# security to what the paired account delegated.
SUPABASE_URL: Final = "https://jpedmcmrsaijlyuiwwcg.supabase.co"
SUPABASE_KEY: Final = "sb_publishable_uLRWOapm8X2JCTJhMcvHWQ_VluAs_RO"

CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_USER_ID: Final = "user_id"
CONF_DELEGATION_ID: Final = "delegation_id"

# Realtime pushes changes within seconds; polling only covers what it
# can't see, like a collaborator's schedule being edited.
POLL_INTERVAL: Final = timedelta(minutes=5)
PAIRING_POLL_INTERVAL: Final = 3

SCOPE_READ: Final = "read"
SCOPE_READ_POINTS: Final = "read_points"

POINT_GAIN: Final = "gain"
POINT_LOSE: Final = "lose"
POINT_BOTTOM: Final = "bottom"
POINT_KINDS: Final = (POINT_GAIN, POINT_LOSE, POINT_BOTTOM)

SERVICE_CHANGE_POINTS: Final = "change_points"
ATTR_KIND: Final = "kind"
ATTR_NOTE: Final = "note"
