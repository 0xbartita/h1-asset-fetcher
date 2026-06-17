"""Immunefi platform plugin (ported from bbscope). Public scraping — no auth."""
from .. import Platform, register
from ...core import log
from ...core.identifiers import SCOPE_TYPES
from . import client


@register
class Immunefi(Platform):
    name = "immunefi"
    label = "Immunefi"
    auth = []          # public, no credentials
    env = {}
    # Immunefi has no public/private or bbp/vdp distinction — filtering is moot.
    filters = [("Everything (public, paying)", "all")]

    def fetch(self, creds, scope, filters, oos):
        return client.fetch(token=None, username=None,
                            prog_filter=filters, asset_types=SCOPE_TYPES[scope],
                            oos=oos, log=log)
