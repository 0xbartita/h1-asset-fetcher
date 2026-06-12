"""HackerOne platform plugin."""
from .. import Platform, Cred, register, PlatformAuthError
from ...core.identifiers import SCOPE_TYPES
from . import client


@register
class HackerOne(Platform):
    name = "hackerone"
    label = "HackerOne"
    auth = [Cred("username"), Cred("token", secret=True)]
    env = {"username": "H1_USERNAME", "token": "H1_API_TOKEN"}

    def fetch(self, creds, scope, filters, oos):
        if not creds.get("username") or not creds.get("token"):
            raise PlatformAuthError(
                "Username and token required for HackerOne. Use -u/-t flags or set "
                "H1_USERNAME / H1_API_TOKEN. Get your token at "
                "https://hackerone.com/settings/api_token/edit")
        session = client.H1Session(creds["username"], creds["token"])
        return client.fetch_all(session, prog_filter=filters,
                                asset_types=SCOPE_TYPES[scope])
