"""Immunefi client: bot-challenge detection and RSC-stream parsing."""
import json

from h1_asset_fetcher.platforms.immunefi import client as ic


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


def test_bot_challenge_detected():
    assert ic._is_bot_challenge(_Resp(403, "<title>Vercel Security Checkpoint</title>"))
    assert ic._is_bot_challenge(_Resp(403, "Just a moment... checking your browser"))
    assert ic._is_bot_challenge(_Resp(503, "challenge-platform cloudflare"))


def test_real_responses_not_flagged_as_challenge():
    assert not ic._is_bot_challenge(_Resp(200, "<html>real content</html>"))
    assert not ic._is_bot_challenge(_Resp(404, "not found"))
    assert not ic._is_bot_challenge(None)


def test_rsc_blob_and_array_parse():
    # Mirror Next.js App Router output: self.__next_f.push([1,"<escaped json>"]).
    payload = ('{"x":1,"bounties":[{"slug":"acme",'
               '"url":"/bug-bounty/acme/information/","project":"Acme"}]}')
    chunk = "self.__next_f.push(" + json.dumps([1, payload], separators=(",", ":")) + ")"
    html = f"<script>{chunk}</script>"
    blob = ic._rsc_blob(html)
    bounties = ic._json_array_after(blob, "bounties")
    assert len(bounties) == 1
    assert bounties[0]["slug"] == "acme"
    assert ic._json_array_after(blob, "missing") == []
