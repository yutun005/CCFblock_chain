"""The launcher must not reopen stale HTML after a new frontend build."""

import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx

from scripts import local_demo
from services.orchestrator import application


def test_launcher_url_tracks_build_bytes_and_preserves_existing_query(tmp_path, monkeypatch):
    monkeypatch.setattr(local_demo, "ROOT", tmp_path)
    index = tmp_path / "apps/web/dist/index.html"
    index.parent.mkdir(parents=True)
    index.write_text('<script src="/assets/new-one.js"></script>')
    first = local_demo.browser_url("http://127.0.0.1:8080/?job=existing&ui=old")
    assert parse_qs(urlparse(first).query)["job"] == ["existing"]
    assert len(parse_qs(urlparse(first).query)["ui"]) == 1
    assert first == local_demo.browser_url("http://127.0.0.1:8080/?job=existing")
    index.write_text('<script src="/assets/new-two.js"></script>')
    assert first != local_demo.browser_url("http://127.0.0.1:8080/?job=existing")


def test_show_demo_opens_the_versioned_url(tmp_path, monkeypatch):
    monkeypatch.setattr(local_demo, "ROOT", tmp_path)
    monkeypatch.setenv("AGENTFLOW_OPEN_BROWSER", "1")
    opened = []
    monkeypatch.setattr(local_demo.webbrowser, "open", opened.append)
    local_demo.show_demo("http://127.0.0.1:8080")
    assert opened == [local_demo.browser_url("http://127.0.0.1:8080")]


def test_html_entry_is_not_cacheable_including_old_job_links(tmp_path, monkeypatch):
    monkeypatch.setattr(application, "ROOT", tmp_path)
    index = tmp_path / "apps/web/dist/index.html"
    index.parent.mkdir(parents=True)
    (index.parent / "assets").mkdir()
    index.write_text("<html>current build</html>", encoding="utf-8")
    engine = SimpleNamespace(db=SimpleNamespace(values=lambda _bucket: []))
    monkeypatch.setattr(application, "Engine", lambda *args, **kwargs: engine)
    config = {"deployment": {}, "market": "", "validator": "", "token": "test", "signers": {}}
    app = application.api_app(config, tmp_path)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path in ("/", "/?job=old-job", "/?ui=current&job=old-job"):
                response = await client.get(path)
                assert response.status_code == 200 and "current build" in response.text
                assert "no-store" in response.headers["cache-control"]
                assert response.headers["pragma"] == "no-cache"
    asyncio.run(check())
