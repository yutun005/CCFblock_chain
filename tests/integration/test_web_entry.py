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


def test_task_output_preview_is_authenticated_current_attempt_and_read_only(tmp_path, monkeypatch):
    db = application.Database(tmp_path / "outputs.sqlite")
    plan = application.data_plan([]).model_dump(mode="json")
    job_id = "0x" + "12" * 32
    db.put("jobs", job_id, {"jobId": job_id, "plan": plan, "inputs": [], "createdAt": 1, "status": "ACTIVE", "deadline": 900, "createArgs": []})
    chain_state = {"attempt": 2, "state": 1}
    engine = SimpleNamespace(db=db, chain=SimpleNamespace(task=lambda *_: chain_state))
    monkeypatch.setattr(application, "Engine", lambda *args, **kwargs: engine)
    config = {"deployment": {}, "market": "", "validator": "", "token": "test", "signers": {}}
    app = application.api_app(config, tmp_path)

    def delivery(job=job_id, task=0, attempt=2, text="当前成果"):
        return {"manifest": {"jobId": job, "taskId": task, "attempt": attempt},
                "artifacts": [application.packet("cleaning.json", text.encode(), "application/json")]}

    db.put("results", "old-attempt", delivery(attempt=1, text="旧轮次"))
    db.put("results", "other-task", delivery(task=1, text="其他任务"))
    db.put("results", "other-job", delivery(job="other", text="其他订单"))
    db.put("execution_frames", "candidate", {"jobId": job_id, "taskId": 0, "attempt": 2,
            "sequence": 1, "action": {"delivery": delivery(text="待检查成果")}})

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            path = f"/api/workspace/jobs/{job_id}/tasks/0/outputs"
            assert (await client.get(path)).status_code == 401
            client.headers["Authorization"] = "Bearer test"
            response = await client.get(path)
            assert response.status_code == 200
            assert response.json()["stage"] == "candidate"
            assert response.json()["files"][0]["text"] == "待检查成果"
            db.put("results", "current", delivery())
            before = {bucket: db.values(bucket) for bucket in ("jobs", "results", "execution_frames", "reviews")}
            response = await client.get(path)
            assert response.json()["stage"] == "submitted"
            assert response.json()["files"][0]["text"] == "当前成果"
            chain_state.update(attempt=3, state=3)
            ended = (await client.get(path)).json()
            assert ended["stage"] == "ended" and ended["files"][0]["text"] == "当前成果"
            for bad in ("/api/workspace/jobs/missing/tasks/0/outputs", f"/api/workspace/jobs/{job_id}/tasks/4/outputs"):
                assert (await client.get(bad)).status_code == 404
            assert before == {bucket: db.values(bucket) for bucket in before}
    asyncio.run(check())
