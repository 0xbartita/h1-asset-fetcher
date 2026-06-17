"""YesWeHack platform plugin (ported from bbscope)."""
from .. import (Platform, Cred, register,
                PRIVATE_BBP, PUBLIC_BBP, ALL_BBP, EVERYTHING)
from ...core import log
from ...core.identifiers import SCOPE_TYPES
from . import client


@register
class YesWeHack(Platform):
    name = "yeswehack"
    label = "YesWeHack"
    # A JWT in YESWEHACK_TOKEN, OR username (email) + YESWEHACK_PASSWORD env var.
    auth = [Cred("token", label="JWT (or use username+YESWEHACK_PASSWORD)", secret=True,
                 required=False),
            Cred("username", label="email (for password login)", required=False)]
    env = {"token": "YESWEHACK_TOKEN", "username": "YESWEHACK_USERNAME"}
    # YesWeHack exposes bounty + public/private flags, but no VDP dimension.
    filters = [PRIVATE_BBP, PUBLIC_BBP, ALL_BBP, EVERYTHING]

    def fetch(self, creds, scope, filters, oos):
        return client.fetch(token=creds.get("token"), username=creds.get("username"),
                            prog_filter=filters, asset_types=SCOPE_TYPES[scope],
                            oos=oos, log=log)
