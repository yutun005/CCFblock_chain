"""Provider-format compatibility must not weaken graph checks or manual edit validation."""
import asyncio
import copy
import json
import pytest
from services.orchestrator import designs
from services.orchestrator.design_format import adapt, decode_response
from tests.integration.test_designs import draft_response, graph, mock_model, service


@pytest.mark.parametrize("wrapper", ["fence", "prose", "encoded", "content_string", "data", "extra_fields"])
def test_complete_drafts_accept_provider_wrappers(service, monkeypatch, wrapper):
    value = draft_response()
    if wrapper == "extra_fields":
        value.update(nextAction="generate_dag", needsInput=False, format="json_object")
        value["graph"]["tasks"][0]["estimatedTime"] = "待确认"
        content = json.dumps(value, ensure_ascii=False)
    elif wrapper == "fence": content = "```JSON\n" + json.dumps(value, ensure_ascii=False) + "\n```"
    elif wrapper == "prose": content = "下面是草稿：\n" + json.dumps(value, ensure_ascii=False) + "\n可以继续修改。"
    elif wrapper == "encoded": content = json.dumps(json.dumps(value, ensure_ascii=False))
    elif wrapper == "content_string": content = json.dumps({"type":"json_object","content":json.dumps(value, ensure_ascii=False)})
    else: content = json.dumps({"data":value}, ensure_ascii=False)
    from unittest.mock import AsyncMock
    from services.orchestrator import planning
    model = AsyncMock(return_value=content)
    monkeypatch.setattr(planning, "completion", model)
    result = asyncio.run(service.message("想办一个读书活动"))
    assert result["graph"]["tasks"] == graph()["tasks"] and model.await_count == 1


def test_minimal_tasks_need_no_full_brief_outline_or_acceptance(service, monkeypatch):
    model = mock_model(monkeypatch, {"reply":"先看草稿", "tasks":[
        {"id":1,"name":"准备材料"}, {"id":2,"name":"整理初稿","depends_on":[1]}]})
    result = asyncio.run(service.message("帮我起草一个工作计划"))
    tasks = result["graph"]["tasks"]
    assert len(tasks) == 2 and tasks[1]["dependencies"] == [tasks[0]["key"]]
    assert result["warnings"] and result["outline"] and model.await_count == 1


def test_graph_nodes_edges_and_non_latin_ids_are_mapped_consistently(service, monkeypatch):
    model = mock_model(monkeypatch, {"message":"可以继续完善", "dag":{"nodes":[
        {"id":"调研节点","data":{"label":"了解需求"}}, {"id":"成稿节点","data":{"label":"编写草稿"}}],
        "edges":[{"source":"调研节点","target":"成稿节点"}]}})
    result = asyncio.run(service.message("先给一个框架"))
    a,b = result["graph"]["tasks"]
    assert a["key"] != b["key"] and b["dependencies"] == [a["key"]]
    assert a["title"] == "了解需求" and b["title"] == "编写草稿" and model.await_count == 1


@pytest.mark.parametrize("reply", ["你好，可以先从目标和现有材料聊起。", {"reply":"这份方案按阶段推进，可以继续问我。"}])
def test_plain_or_reply_only_chat_keeps_current_graph(service, monkeypatch, reply):
    first = service.apply_template("starter-event", 1)
    from unittest.mock import AsyncMock
    from services.orchestrator import planning
    content = reply if isinstance(reply,str) else json.dumps(reply,ensure_ascii=False)
    model = AsyncMock(return_value=content)
    monkeypatch.setattr(planning,"completion",model)
    result = asyncio.run(service.message("你好，介绍一下当前方案",first["id"],1))
    assert result["graph"] == first["graph"] and result["brief"] == first["brief"]
    assert result["messages"][-1]["content"] and model.await_count == 1


def test_sparse_task_updates_preserve_existing_dependencies_outputs_and_acceptance(service, monkeypatch):
    first = service.apply_template("starter-product", 1)
    tasks = [{"key":task["key"],"title":task["title"]} for task in first["graph"]["tasks"]]
    tasks[0]["title"] = "重新研究需求"
    mock_model(monkeypatch,{"reply":"已修改任务名称", "graph":{"tasks":tasks}})
    result = asyncio.run(service.message("把第一个任务改为重新研究需求",first["id"],1))
    expected = copy.deepcopy(first["graph"])
    expected["tasks"][0]["title"] = tasks[0]["title"]
    assert result["graph"] == expected


def test_cycles_are_not_hidden_by_format_adaptation(service, monkeypatch):
    first = service.apply_template("starter-learning",1)
    bad = {"reply":"改图", "graph":{"tasks":[{"id":"a","title":"甲","deps":["b"]},{"id":"b","title":"乙","deps":["a"]}]}}
    mock_model(monkeypatch,bad,bad)
    with pytest.raises(ValueError): asyncio.run(service.message("调整学习步骤",first["id"],1))
    assert service.get(first["id"]) == first


def test_no_task_nodes_never_become_a_fabricated_success(service, monkeypatch):
    mock_model(monkeypatch,{"reply":"我已经出图了"},{"reply":"下一轮出图"})
    with pytest.raises(ValueError): asyncio.run(service.message("先生成DAG"))
    assert not service.db.values("design_sessions")


def test_review_cannot_treat_generated_graph_as_a_successful_audit():
    data = adapt("ReviewResult", decode_response(json.dumps({"type":"json_object","content":{"reply":"已出图","graph":graph()}})),{})
    with pytest.raises(ValueError): designs.ReviewResult.model_validate(data)


def test_model_compatibility_is_not_used_for_manual_edit_payloads():
    value = {"expectedRevision":1,"graph":graph(),"extra":"not allowed"}
    with pytest.raises(ValueError): designs.EditInput.model_validate(value)


@pytest.mark.parametrize("message", ["你好", "您好！", "你是谁？", "介绍一下你自己", "谢谢"])
def test_initial_greetings_do_not_force_task_graph_schema(service, monkeypatch, message):
    model = mock_model(monkeypatch,{"reply":"你好，可以告诉我你的想法，或先问我怎么用。"})
    result = asyncio.run(service.message(message))
    assert result["graph"] is None and result["messages"][-1]["content"].startswith("你好")
    assert model.await_count == 1


def test_task_array_is_accepted_as_a_real_draft(service, monkeypatch):
    mock_model(monkeypatch,[{"title":"整理材料"},{"title":"编写初稿","dependencies":["task1"]}])
    result = asyncio.run(service.message("先生成草稿"))
    assert len(result["graph"]["tasks"]) == 2


def test_greeting_does_not_become_the_goal_of_a_later_real_plan(service, monkeypatch):
    model = mock_model(monkeypatch,{"reply":"你好"},{"graph":{"tasks":[{"title":"准备活动"}]}})
    first = asyncio.run(service.message("你好"))
    second = asyncio.run(service.message("我要规划一场科学活动",first["id"],1))
    assert second["goal"] == "我要规划一场科学活动"
    context = json.loads(model.call_args.args[2][1]["content"])
    assert context["originalGoal"] == second["goal"] and context["currentBrief"] is None


@pytest.mark.parametrize("reference", ["准备材料", {"taskKey":"task1"}])
def test_dependencies_can_use_unambiguous_titles_or_task_key_objects(service, monkeypatch, reference):
    mock_model(monkeypatch,{"tasks":[{"title":"准备材料"},{"title":"写初稿","dependencies":[reference]}]})
    result = asyncio.run(service.message("先做一个简单方案"))
    assert result["graph"]["tasks"][1]["dependencies"] == [result["graph"]["tasks"][0]["key"]]


def test_running_backend_exposes_format_version_for_reload_checks(service):
    import httpx
    from fastapi import FastAPI
    app = FastAPI()
    designs.install(app, service.workspace)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            response = await client.get('/api/workspace/design-format')
            assert response.status_code == 200 and response.json()['version'] == 'compact-response-v1'
    asyncio.run(run())
