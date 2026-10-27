from copy import deepcopy
from types import SimpleNamespace

from services.orchestrator.application import Database, data_plan
from services.orchestrator.workspace import task_summaries


def test_whole_task_summaries_keep_runs_separate_and_do_not_modify_records(tmp_path):
    db = Database(tmp_path / "tasks.sqlite")
    engine = SimpleNamespace(db=db)
    first, second, legacy = ["0x" + c * 64 for c in ["a", "b", "c"]]
    for job_id in [first, second, legacy]:
        db.put("jobs", job_id, {"jobId": job_id, "status": "ACTIVE", "createdAt": 1,
                               "inputs": [{"private": "csv"}], "createArgs": ["frozen"],
                               "deadline": "9999999999", "plan": {**data_plan([]).model_dump(mode="json"), "goal": "杭州三日旅行"}})
    db.put("design_sessions", "design", {"title": "朋友两人的杭州旅行"})
    for job_id in [first, second]:
        db.put("versions", job_id, {"jobId": job_id, "projectId": "same-project", "proposalId": job_id,
                                   "recipe": {"title": "我的项目" if job_id == first else "已确认的原任务名称", "outputs": ["attractions", "report"]}})
        db.put("proposals", job_id, {"planning": {"source": {"designId": "design"}}})
    db.put("reviews", "r1", {"jobId": first, "status": "AWAITING_REVIEW"})
    db.put("reviews", "r2", {"jobId": second, "status": "ACCEPTED"})
    buckets = ["jobs", "versions", "proposals", "reviews", "design_sessions"]
    before = deepcopy({b: db.values(b) for b in buckets})
    result = {r["jobId"]: r for r in task_summaries(engine)}
    assert len(result) == 3
    assert result[first]["title"] == "朋友两人的杭州旅行"
    assert result[first]["designId"] == "design"
    assert result[first]["reviewStatus"] == "AWAITING_REVIEW"
    assert result[second]["reviewStatus"] is None
    assert result[second]["title"] == "已确认的原任务名称"
    assert result[first]["finalTaskKeys"] == ["attractions", "report"]
    assert result[legacy]["title"] == "杭州三日旅行"
    assert result[legacy]["designId"] is None
    assert result[legacy]["finalTaskKeys"] is None
    assert all("inputs" not in r and "createArgs" not in r for r in result.values())
    assert {b: db.values(b) for b in buckets} == before


def test_no_history_is_an_empty_collection(tmp_path):
    assert task_summaries(SimpleNamespace(db=Database(tmp_path / "empty.sqlite"))) == []
