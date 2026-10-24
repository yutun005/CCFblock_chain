"""Frozen workspace terms and deterministic, reviewable service deliverables."""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from html import escape
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Terms(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tier: Literal["basic", "standard", "detailed"] = "standard"
    outputs: list[Literal["report", "charts"]] = Field(default_factory=list, max_length=2)
    group_by: Literal["organization_quarter", "organization", "category", "month"] = "organization_quarter"
    review_seconds: int = Field(default=1800, ge=60, le=3300, strict=True)
    reused: list[dict] = Field(default_factory=list, max_length=15)
    communication: Literal["execution-dialogue-v1"] | None = Field(default=None, exclude_if=lambda v: v is None)
    # Decode historical orders only. The removed coordination workflow is never started.
    coordination: Literal["travel-candidates-v1"] | None = Field(default=None, exclude_if=lambda v: v is None)
    coordination_fixed: dict | None = Field(default=None, exclude_if=lambda v: v is None)


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    changes: list[dict] = Field(default_factory=list, max_length=2)


REPORT_SKILLS = {"report_generation", "travel_report"}


def needs_review(spec):
    return spec.skill in REPORT_SKILLS and (not spec.options or "report" in spec.options.get("outputs", []))


def review_mask(plan):
    return sum(1 << i for i, spec in enumerate(plan.topological()) if needs_review(spec))


def price_for(base: int, spec) -> int:
    if not spec.options or spec.skill not in REPORT_SKILLS:
        return base
    terms = Terms.model_validate(spec.options)
    # The quote always binds these output terms through the requirement hash.
    pieces = (2 if "report" in terms.outputs else 0) + (1 if "charts" in terms.outputs else 0)
    if spec.skill == "travel_report":
        pieces = 3
    if terms.tier == "detailed" and "report" in terms.outputs:
        pieces += 2
    return max(1, (base * pieces + 2) // 3)


def normalize_change(target: str, text: str, action: str, *, travel: bool) -> dict:
    targets = (
        {"需求与资料", "住宿", "每日行程", "费用", "限制与来源"}
        if travel
        else {"数据与质量", "统计结果", "季度统计", "结论与限制", "来源", "revenue.svg"}
    )
    if target not in targets or not 1 <= len(text.strip()) <= 1500:
        raise ValueError("请选择报告中的章节或图表，并填写 1–1500 字修改意见")
    if re.search(
        r"(换|替换|补充|新增|添加).{0,10}(数据|材料|指标)|利润|预测|回归|同比|环比|另.*语言|翻译|英文|英语|多语|天数|增加.*天|改.*\d.*天",
        text,
    ):
        raise ValueError("这项修改改变了原约定范围，请基于此版本创建续单，先确认新增费用")
    if action not in {"clarify", "shorten", "costs", "chronological", "chart_labels"}:
        raise ValueError("请选择支持的修改方式；未识别的要求请在续单中调整")
    allowed = {
        "每日行程": {"chronological", "shorten"},
        "费用": {"costs", "shorten"},
        "结论与限制": {"clarify", "shorten"},
        "revenue.svg": {"chart_labels"},
    }
    if action not in allowed.get(target, {"shorten"}):
        raise ValueError("该章节不支持此修改方式，请调整位置或修改方式")
    return {"target": target, "text": text.strip(), "action": action}


def grouped_statistics(inputs, group_by):
    from services.orchestrator.application import artifact_json, rows_from, unpack

    groups = defaultdict(lambda: {"count": 0, "quantity": 0, "revenue_cents": 0})
    for _, _, row in rows_from(inputs):
        key = {
            "organization": row["organization"],
            "category": row["category"],
            "month": row["date"][:7],
            "organization_quarter": row["organization"]
            + f"/{row['date'][:4]}-Q{(int(row['date'][5:7]) - 1) // 3 + 1}",
        }[group_by]
        group = groups[key]
        group["count"] += 1
        group["quantity"] += int(row["quantity"])
        group["revenue_cents"] += int(row["quantity"]) * int(row["unit_price_cents"])
    for group in groups.values():
        group["mean_order_cents"] = {"numerator": group["revenue_cents"], "denominator": group["count"]}
    quality = artifact_json(unpack(next(x for x in inputs if x["name"] == "validation.json")))
    return {
        "groups": dict(sorted(groups.items())),
        "group_by": group_by,
        "quality_invalid_rows": quality["invalid_rows"],
        "total_orders": sum(x["count"] for x in groups.values()),
        "total_revenue_cents": sum(x["revenue_cents"] for x in groups.values()),
    }


def replace_section(body, title, content):
    pattern = r"(## " + re.escape(title) + r"\n).*?(?=\n## |\Z)"
    return re.sub(pattern, lambda match: match[1] + "\n" + content.strip() + "\n", body, flags=re.S)


def render_report(spec, inputs, revision=None):
    from services.orchestrator.application import (
        artifact_json,
        canonical,
        packet,
        report,
        travel_report,
        unpack,
    )

    terms = Terms.model_validate(spec.options or {"outputs": ["report", "charts"]})
    revision = revision or {}
    communication = revision.get("communication")
    changes = Revision.model_validate({k: v for k, v in revision.items() if k != "communication"}).changes
    travel = spec.skill == "travel_report"
    artifacts = (travel_report if travel else report)(inputs)
    files = {x["name"]: unpack(x) for x in artifacts}
    name = "travel-report.md" if travel else "report.md"
    body = files[name].decode()
    if communication:
        from .communication import render_notes
        body += "\n## 执行依据\n\n" + render_notes(communication["inquiry"]["topic"], communication["response"]["answer"]) + "\n"
        files["execution-explanation.json"] = canonical(communication)
    if not travel:
        body = body.replace("## 季度统计", "## 统计结果")
    source = next(x for x in inputs if x["name"] == ("itinerary.json" if travel else "statistics.json"))
    data = artifact_json(unpack(source))
    if not travel and data.get("group_by", "organization_quarter") != "organization_quarter":
        grouping = {"organization": "组织", "category": "商品类别", "month": "月份"}[data["group_by"]]
        body = body.replace("组织与季度", grouping).replace("季度销售额", "分组销售额")
        if "revenue.svg" in files:
            files["revenue.svg"] = files["revenue.svg"].decode().replace("季度销售额", "分组销售额").encode()
    for raw in changes:
        change = normalize_change(raw["target"], raw["text"], raw["action"], travel=travel)
        target, action = change["target"], change["action"]
        if target == "季度统计":
            target = "统计结果"
        if action == "clarify":
            top = max(data["groups"], key=lambda k: data["groups"][k]["revenue_cents"], default=None)
            finding = (
                f"销售额最高的分组是 {top}，为 {data['groups'][top]['revenue_cents']} 分。"
                if top
                else "没有有效订单。"
            )
            content = (
                f"主要发现：{finding}\n有效订单共 {data['total_orders']} 笔，"
                f"总销售额 {data['total_revenue_cents']} 分。\n"
                "本结论仅描述所提供的数据，不推断原因或未来业绩。"
            )
        elif action == "costs":
            tickets = sum(day["ticket_cents"] for day in data["days"])
            transport = sum(day["transport_cents"] for day in data["days"])
            content = (
                f"住宿 {data['hotel']['total_cents']} 分 + 门票 {tickets} 分 + 交通 {transport} 分"
                f" = 合计 {data['total_cents']} 分。\n预算 {data['request']['budget_cents']} 分，"
                f"余额 {data['remaining_cents']} 分。\n不含餐饮和往返目的地费用；价格来自资料快照。"
            )
        elif action == "chronological":
            content = "\n\n".join(
                f"### 第 {i + 1} 天 · {day['date']}\n"
                + "\n".join(
                    f"{stop['arrive']}–{stop['depart']}：{stop['name']}（门票 {stop['ticket_cents']} 分）"
                    for stop in day["stops"]
                )
                + f"\n{day['return_at']} 返回酒店；当日交通 {day['transport_cents']} 分。"
                for i, day in enumerate(data["days"])
            )
        elif action == "chart_labels":
            svg = (
                files["revenue.svg"]
                .decode()
                .replace('font-size="14"', 'font-size="16"')
                .replace('fill="#087f8c"', 'fill="#174da1"')
            )
            files["revenue.svg"] = svg.encode()
            content = None
        else:
            if target == "结论与限制":
                content = (
                    f"{data['total_orders']} 笔有效订单；总销售额 {data['total_revenue_cents']} 分。"
                    "仅反映本次输入数据。"
                )
            elif target == "数据与质量":
                content = f"有效订单 {data['total_orders']} 笔；原始异常 {data['quality_invalid_rows']} 行。"
            elif target == "住宿":
                hotel = data["hotel"]
                content = (
                    f"{hotel['name']} · {hotel['rooms']} 间 · {hotel['nights']} 晚"
                    f" · 共 {hotel['total_cents']} 分。"
                )
            elif target == "限制与来源":
                content = (
                    "资料快照，非实时查询或预订。费用仅含住宿、门票和所列交通，不含餐饮及往返目的地。\n"
                    f"行程：{source['sha256']}\n原始资料：{data['input_hash']}"
                )
            else:
                match = re.search(r"## " + re.escape(target) + r"\n(.*?)(?=\n## |\Z)", body, re.S)
                lines = [line.strip() for line in match[1].splitlines() if line.strip()]
                # Keep every number and source; compress explanatory prose and whitespace.
                content = "\n".join(dict.fromkeys(lines))
                content = (
                    content.replace("资料来源：", "来源：")
                    .replace("行程合计：", "合计：")
                    .replace("预算余额：", "余额：")
                )
                content = content.replace(
                    "本报告使用输入数据；结论仅描述输入数据，不代表真实企业业绩。", "结论仅描述输入数据。"
                )
                content = content.replace("统计成果 SHA-256：", "统计来源：").replace("组织与季度", "分组")
                if target == "统计结果":
                    content = content.replace("订单数", "订单").replace("销售额（分）", "金额（分）")
        if content is not None:
            body = replace_section(body, target, content)
    files[name] = body.encode()
    if not travel:
        refs = artifact_json(files["references.json"])
        from services.orchestrator.application import sha256

        refs["charts"] = (
            {key: sha256(files[key]) for key in ("revenue.svg", "chart-data.json")}
            if "charts" in terms.outputs
            else {}
        )
        files["references.json"] = canonical(refs)
        if "charts" not in terms.outputs:
            files.pop("revenue.svg")
            files.pop("chart-data.json")
            files[name] = (
                re.sub(r"!\[[^\]]*\]\(revenue\.svg\)", "", body)
                .replace("图表数据：[chart-data.json](chart-data.json)", "")
                .encode()
            )
        if "report" not in terms.outputs:
            files.pop(name)
            files.pop("references.json")
    if terms.tier == "detailed" and "report" in terms.outputs:
        rows = [("项目", "数值（分）", "来源 SHA-256")]
        if travel:
            rows.extend(
                [
                    ("住宿", str(data["hotel"]["total_cents"]), source["sha256"]),
                    ("门票", str(sum(d["ticket_cents"] for d in data["days"])), source["sha256"]),
                    ("交通", str(sum(d["transport_cents"] for d in data["days"])), source["sha256"]),
                    ("合计", str(data["total_cents"]), source["sha256"]),
                ]
            )
        else:
            rows.extend(
                (key, str(group["revenue_cents"]), source["sha256"]) for key, group in data["groups"].items()
            )
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        # Neutralize spreadsheet formula interpretation in user-controlled labels.
        writer.writerows(
            tuple("'" + v if v.startswith(("=", "+", "-", "@")) else v for v in row) for row in rows
        )
        files["source-comparison.csv"] = stream.getvalue().encode("utf-8-sig")
        table = "".join("<tr>" + "".join("<td>" + escape(c) + "</td>" for c in row) + "</tr>" for row in rows)
        chart = files.get("revenue.svg", b"").decode()
        files["report.html"] = (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width'>"
            "<title>离线成果报告</title><style>body{font:16px/1.7 system-ui;"
            "max-width:1000px;margin:32px auto;padding:16px;color:#172b4d}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}"
            "table{border-collapse:collapse;width:100%}"
            "td{border:1px solid #bbc;padding:8px;overflow-wrap:anywhere}"
            "svg{max-width:100%;height:auto}</style>"
            + "<h1>成果报告</h1><pre>"
            + escape(files[name].decode())
            + "</pre>"
            + chart
            + "<h2>关键数据与来源</h2><table>"
            + table
            + "</table></html>"
        ).encode()
    if needs_review(spec):
        files["revision.json"] = canonical(
            {"revision": len(changes), "changes": changes, "source": source["sha256"]}
        )
    media = {
        "md": "text/markdown",
        "json": "application/json",
        "svg": "image/svg+xml",
        "html": "text/html",
        "csv": "text/csv",
    }
    return [packet(key, value, media[key.rsplit(".", 1)[-1]]) for key, value in files.items()]


def execute_product(spec, inputs, revision=None):
    from services.orchestrator.application import EXECUTORS, canonical, packet

    if spec.skill in {"attraction_search", "hotel_search"}:
        from .travel import search_inputs, require_candidates

        resolved = search_inputs(inputs)
        artifacts = EXECUTORS[spec.skill](resolved)
        if resolved is not inputs:
            require_candidates(spec.skill, artifacts)
        return artifacts
    if spec.skill in REPORT_SKILLS:
        return render_report(spec, inputs, revision)
    if spec.skill == "statistical_analysis" and spec.options:
        terms = Terms.model_validate(spec.options)
        return [
            packet(
                "statistics.json", canonical(grouped_statistics(inputs, terms.group_by)), "application/json"
            )
        ]
    return EXECUTORS[spec.skill](inputs)


def verify_product(spec, inputs, artifacts, revision=None):
    from services.orchestrator.application import artifact_json, reference, unpack, verify_content

    if spec.skill in {"attraction_search", "hotel_search"}:
        from .travel import search_inputs, require_candidates

        resolved = search_inputs(inputs)
        checks = verify_content(spec.skill, resolved, artifacts)
        if resolved is not inputs:
            require_candidates(spec.skill, artifacts)
        return checks
    if spec.skill not in REPORT_SKILLS and not (spec.skill == "statistical_analysis" and spec.options):
        return verify_content(spec.skill, inputs, artifacts)
    if spec.skill == "statistical_analysis":
        # Separate conservation checks over the cleaned source, in addition to grouped reconstruction.
        rows = list(
            csv.DictReader(
                io.StringIO(unpack(next(p for p in inputs if p["name"] == "cleaned.csv")).decode())
            )
        )
        stats = artifact_json(unpack(artifacts[0]))
        if stats["total_orders"] != len(rows) or stats["total_revenue_cents"] != sum(
            int(r["quantity"]) * int(r["unit_price_cents"]) for r in rows
        ):
            raise ValueError("statistics row/revenue conservation failed")
    support = next((p for p in artifacts if p["name"] == "execution-explanation.json"), None)
    if support:
        revision = {**(revision or {}), "communication": artifact_json(unpack(support))}
    expected = execute_product(spec, inputs, revision)
    if [reference(p) for p in artifacts] != [reference(p) for p in expected]:
        raise ValueError("deliverables do not match frozen scope, source data or requested revision")
    return [
        "frozen_deliverable_set",
        "source_numbers_and_hashes",
        "revision_changes",
        "offline_html_no_external_assets",
    ]
