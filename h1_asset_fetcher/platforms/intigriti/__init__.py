"""Intigriti platform plugin (ported from bbscope)."""
from .. import Platform, Cred, register
from ...core import log
from ...core.identifiers import SCOPE_TYPES
from . import client


@register
class Intigriti(Platform):
    name = "intigriti"
    label = "Intigriti"
    auth = [Cred("token", label="researcher API token", secret=True)]
    env = {"token": "INTIGRITI_TOKEN"}

    def fetch(self, creds, scope, filters, oos):
        return client.fetch(token=creds.get("token"), username=creds.get("username"),
                            prog_filter=filters, asset_types=SCOPE_TYPES[scope],
                            oos=oos, log=log)
