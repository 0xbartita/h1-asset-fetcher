"""Drive the Textual TUI headlessly with a fake platform that returns the
fixture programs — proves the progressive flow fetches and populates the table
without credentials or network."""
import asyncio
import json

from conftest import FIXTURES
from h1_asset_fetcher.platforms import Platform, Cred


class _FakePlatform(Platform):
    name = "fake"
    label = "Fake"
    auth = [Cred("token", secret=True)]

    def fetch(self, creds, scope, filters, oos):
        return json.loads((FIXTURES / "cache.json").read_text())


def test_tui_fetch_populates_table(monkeypatch):
    from h1_asset_fetcher.tui import app as appmod
    monkeypatch.setattr(appmod, "get_platform", lambda name: _FakePlatform())

    async def scenario():
        application = appmod.H1FetcherApp()
        async with application.run_test(size=(120, 60)) as pilot:
            # 1. platform
            application.query_one("#platform-select", appmod.Select).value = "hackerone"
            application.query_one("#platform-next", appmod.Button).press()
            await pilot.pause()
            assert application.query_one("#sec-platform").has_class("done")
            assert not application.query_one("#sec-creds").has_class("locked")

            # 2. credentials (field built from the fake platform's auth descriptor)
            application.query_one("#cred-token", appmod.Input).value = "x"
            application.query_one("#creds-next", appmod.Button).press()
            await pilot.pause()

            # 3. fetch (default scope android, oos off)
            application.query_one("#scope-fetch", appmod.Button).press()
            table = application.query_one("#results-table", appmod.DataTable)
            for _ in range(60):
                await pilot.pause()
                if table.row_count:
                    break
                await asyncio.sleep(0.05)

            # in-scope, non-OOS android assets: app, free, globex (beta is OOS)
            assert table.row_count == 3
            assert not application.query_one("#sec-results").has_class("locked")

    asyncio.run(scenario())
