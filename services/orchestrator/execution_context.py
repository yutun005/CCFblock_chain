"""Read-only, source-preserving context across the current order's dependency graph."""
from __future__ import annotations


def ancestors(plan, task_key):
    tasks = {t.key: t for t in plan.tasks}
    distances, queue = {}, [(key, 1) for key in tasks[task_key].dependencies]
    while queue:
        key, depth = queue.pop(0)
        if key in distances and distances[key] <= depth:
            continue
        distances[key] = depth
        queue.extend((parent, depth + 1) for parent in tasks[key].dependencies)
    return distances


def index(engine, plan, payload):
    from .application import reference
    tasks = plan.topological()
    depths = ancestors(plan, tasks[payload["taskId"]].key)
    resources, directory = [], []
    for task_id, task in enumerate(tasks):
        state = engine.chain.task(payload["jobId"], task_id)
        directory.append({"key": task.key, "title": task.title, "skill": task.skill,
                          "dependencies": list(task.dependencies),
                          "status": {0: "尚未接单", 1: "执行中", 2: "已交付", 3: "已取消"}.get(state["state"], "已结束"),
                          "canDiscuss": state["state"] == 1, "canReadResult": state["state"] == 2})
        if task.key not in depths or state["state"] != 2:
            continue
        result = engine.db.get("results", state["resultHash"])
        if result:
            resources.append({"taskKey": task.key, "title": task.title, "depth": depths[task.key],
                              "attempt": state["attempt"], "resultHash": state["resultHash"],
                              "artifacts": [reference(p) for p in result["artifacts"]]})
    return {"contextIndex": sorted(resources, key=lambda r: (r["depth"], r["taskKey"])),
            "taskDirectory": directory}


def retrieve(engine, plan, payload, query):
    from .application import unpack, reference
    tasks = plan.topological()
    target = query.get("taskKey")
    if target not in ancestors(plan, tasks[payload["taskId"]].key):
        raise ValueError("只能读取本任务前置链条中的成果")
    task_id = next(i for i, task in enumerate(tasks) if task.key == target)
    state = engine.chain.task(payload["jobId"], task_id)
    result = engine.db.get("results", state["resultHash"]) if state["state"] == 2 else None
    if not result:
        raise ValueError("所需前置成果尚未交付")
    names = query.get("names", [])
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ValueError("请按成果目录提供文件名列表")
    files = {p["name"]: p for p in result["artifacts"]}
    if set(names) - files.keys():
        raise ValueError("请求的文件不在这项前置成果中")
    selected = [files[name] for name in dict.fromkeys(names)] if names else list(files.values())
    source = {"taskKey": target, "taskId": task_id, "attempt": state["attempt"], "resultHash": state["resultHash"]}
    # Small business artifacts retain exact bytes for the worker's file tools.
    # Large files are read in pages, never silently cut and presented as complete.
    if sum(len(p["data"]) for p in selected) <= 500_000 and not query.get("offset"):
        return {"source": source, "artifacts": selected}
    offset = query.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("读取位置必须是非负整数")
    chunks = []
    for packet in selected:
        text = unpack(packet).decode("utf-8", errors="replace")
        chunks.append({**reference(packet), "offset": offset, "text": text[offset:offset + 4000],
                       "hasMore": offset + 4000 < len(text)})
    return {"source": source, "chunks": chunks}
