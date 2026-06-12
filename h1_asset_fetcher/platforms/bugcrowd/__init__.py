"""Bugcrowd platform plugin (ported from bbscope)."""
from .. import Platform, Cred, register
from ...core import log
from ...core.identifiers import SCOPE_TYPES
from . import client


@register
class Bugcrowd(Platform):
    name = "bugcrowd"
    label = "Bugcrowd"
    auth = [Cred("token", label="_bugcrowd_session cookie", secret=True)]
    env = {"token": "BUGCROWD_TOKEN"}

    def fetch(self, creds, scope, filters, oos):
        return client.fetch(token=creds.get("token"), username=creds.get("username"),
                            prog_filter=filters, asset_types=SCOPE_TYPES[scope],
                            oos=oos, log=log)
