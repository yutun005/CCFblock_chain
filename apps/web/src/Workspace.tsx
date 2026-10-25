import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { amount as money, price } from "./amount";
import {
  PlanningGraph,
  PlanReview,
  RequirementReview,
  TemplateLibrary,
} from "./TaskPlanning";
import ServiceGraph, { evidenceLabels } from "./ServiceGraph";
import {
  ArrowLeft,
  ArrowRight,
  FileUp,
  FolderOpen,
  LayoutTemplate,
  Sparkles,
  Check,
  X,
} from "lucide-react";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
const prefix = "/api/workspace";
const preferences: Record<string, string> = {
  lowest_price: "费用优先",
  fastest: "速度优先",
  reliability: "履约优先",
  balanced: "费用优先",
};
const labels: Record<string, string> = {
  clean: "清洗数据",
  validate: "数据质量检查",
  statistics: "统计结果",
  charts: "图表",
  report: "分析报告",
  attractions: "景点推荐",
  hotels: "酒店推荐",
  route: "路线安排",
};
const statuses: Record<string, string> = {
  ACTIVE: "执行中",
  SUCCEEDED: "已完成",
  PARTIAL_FAILED: "部分结束",
  INTERRUPTED: "执行中断",
  PREPARING: "准备中",
  AWAITING_REVIEW: "等待确认",
  REWORKING: "正在修改",
  ACCEPTED: "已经接受",
  REJECTED: "已拒绝",
  TIMED_OUT: "确认超时",
  CANCELED: "已取消",
  BIDDING: "询价中",
  WAITING_AGENT: "等待商家空闲",
  WAITING_INPUT: "等待服务补充信息",
  WAITING_DEPENDENCIES: "等待前置成果",
  ASSIGNING: "正在委派",
  RUNNING: "执行中",
  VERIFYING: "校验中",
  SETTLING: "结算中",
  SETTLED: "已结算",
  RECOVERING: "等待本轮失效",
  REASSIGNING: "重新委派",
};
const groupLabels: Record<string, string> = {
  organization_quarter: "组织与季度",
  organization: "组织",
  category: "商品类别",
  month: "月份",
};
const initial = {
  title: "",
  scenario: "cross_org_analysis",
  materialIds: [] as string[],
  outputs: [] as string[],
  tier: "standard",
  preference: "lowest_price",
  budget: "100",
  groupBy: "organization_quarter",
  requirement: "",
  planningMode: "llm",
  planningId: null,
  forceRedo: [] as string[],
  reviewSeconds: 1800,
};

// Keep content fingerprints in the original files and API, out of user previews.
const fingerprint = /sha[\s_-]?256|\b(?:0x)?[a-f0-9]{64}\b/i;
function reportText(text: string) {
  return text
    .split("\n")
    .filter((line) => !fingerprint.test(line))
    .join("\n");
}

function previewData(value: unknown) {
  return JSON.stringify(
    value,
    (key, item) =>
      /hash|sha[\s_-]?256/i.test(key) ||
      (typeof item === "string" && fingerprint.test(item))
        ? undefined
        : item,
    2,
  );
}

function previewFile(text: string, name: string) {
  if (name.endsWith(".json")) {
    try {
      return previewData(JSON.parse(text));
    } catch {
      return reportText(text);
    }
  }
  // CSV previews retain business columns when the source fingerprint is removed.
  return text
    .split("\n")
    .map((line) => {
      const fields = line.match(/(?:^|,)(?:"(?:[^"]|"")*"|[^,\r]*)/g) || [];
      return fields
        .map((field) => field.replace(/^,/, ""))
        .filter((field) => !fingerprint.test(field))
        .join(",");
    })
    .join("\n");
}

function previewMarkup(text: string, svg = false) {
  const doc = new DOMParser().parseFromString(
    text,
    svg ? "image/svg+xml" : "text/html",
  );
  doc.querySelectorAll("table").forEach((table) => {
    const rows = Array.from(table.querySelectorAll("tr"));
    const columns = Array.from(rows[0]?.children || []).flatMap(
      (cell, index) =>
        fingerprint.test(cell.textContent || "") ? [index] : [],
    );
    rows.forEach((row) =>
      columns
        .slice()
        .reverse()
        .forEach((index) => row.children[index]?.remove()),
    );
  });
  doc.querySelectorAll("pre").forEach((element) => {
    element.textContent = reportText(element.textContent || "");
  });
  doc
    .querySelectorAll("p, li, td, th, text, tspan, title, desc")
    .forEach((element) => {
      if (fingerprint.test(element.textContent || "")) element.remove();
    });
  return new XMLSerializer().serializeToString(doc);
}

function Report({ text }: { text: string }) {
  const lines = reportText(text).split("\n"),
    blocks: ReactNode[] = [];
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((value) => value.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i],
      key = i;
    if (
      line.trim().startsWith("|") &&
      /^\s*\|?[\s:|-]+\|\s*$/.test(lines[i + 1] || "")
    ) {
      const header = cells(line),
        rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|"))
        rows.push(cells(lines[i++]));
      i--;
      blocks.push(
        <div className="report-table" key={key}>
          <table>
            <thead>
              <tr>
                {header.map((cell, n) => (
                  <th key={n} scope="col">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, n) => (
                <tr key={n}>
                  {row.map((cell, m) => (
                    <td key={m}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (line.startsWith("# "))
      blocks.push(<h2 key={key}>{line.slice(2)}</h2>);
    else if (line.startsWith("## "))
      blocks.push(<h3 key={key}>{line.slice(3)}</h3>);
    else if (line.startsWith("### "))
      blocks.push(<h4 key={key}>{line.slice(4)}</h4>);
    else if (line && !line.startsWith("!["))
      blocks.push(
        <p key={key}>{line.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")}</p>,
      );
  }
  return <div className="report-body">{blocks}</div>;
}

const checkLabels: Record<string, string> = {
  signature: "执行者签名有效",
  input_lineage: "输入来源一致",
  frozen_deliverable_set: "约定文件齐全",
  source_numbers_and_hashes: "数值与来源校验通过",
  revision_changes: "修改要求已核验",
  offline_html_no_external_assets: "离线报告无需外部资源",
};

function Draft({ value }: { value: Row | null }) {
  if (!value) return <p role="status">正在读取报告…</p>;
  return (
    <div className="draft-preview">
      {value.files
        .filter((p: Row) => p.name.endsWith(".md"))
        .map((p: Row) => (
          <Report key={p.name} text={p.text} />
        ))}
      {value.files
        .filter((p: Row) => p.name.endsWith(".svg"))
        .map((p: Row) => (
          <img
            key={p.name}
            className="report-chart"
            src={
              "data:image/svg+xml;charset=utf-8," +
              encodeURIComponent(previewMarkup(p.text, true))
            }
            alt="销售额图表；对应数值可在下方关联数据查看"
          />
        ))}
      {value.files
        .filter((p: Row) => p.name.endsWith(".html"))
        .map((p: Row) => (
          <details key={p.name}>
            <summary>预览离线网页报告</summary>
            <iframe
              title="离线网页报告"
              sandbox=""
              srcDoc={previewMarkup(p.text)}
            />
          </details>
        ))}
      <details>
        <summary>关联数据、引用资料与版本记录</summary>
        {[...value.files, ...(value.related || [])]
          .filter((p: Row) => /\.(json|csv)$/.test(p.name))
          .map((p: Row) => (
            <section key={p.name}>
              <h4>{p.name}</h4>
              <pre>{previewFile(p.text, p.name)}</pre>
            </section>
          ))}
      </details>
    </div>
  );
}

export default function Workspace({
  api,
  token,
  currentJob,
  onJob,
}: {
  api: Api;
  token: string;
  currentJob: string;
  onJob: (id: string) => void;
}) {
  const [page, setPage] = useState("editor"),
    [recipe, setRecipe] = useState<Row>(initial);
  const [materials, setMaterials] = useState<Row[]>([]),
    [projects, setProjects] = useState<Row[]>([]);
  const [offers, setOffers] = useState<Row[]>([]),
    [chosen, setChosen] = useState<Row | null>(null);
  const [unavailable, setUnavailable] = useState<Row[]>([]);
  const [frontier, setFrontier] = useState<Row | null>(null);
  const [unknownHistory, setUnknownHistory] = useState<string[]>([]);
  const [version, setVersion] = useState<Row | null>(null),
    [compare, setCompare] = useState<Row | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Row>>({}),
    [reviews, setReviews] = useState<Row[]>([]);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState("");
  const [selectedDraft, setSelectedDraft] = useState<Record<string, number>>(
    {},
  );
  const [target, setTarget] = useState("结论与限制"),
    [revisionAction, setRevisionAction] = useState("shorten"),
    [feedback, setFeedback] = useState("");
  const [planner, setPlanner] = useState<Row | null>(null);
  const [travelIntent, setTravelIntent] = useState<Row | null>(null);
  const [travelSource, setTravelSource] = useState<Row | null>(null);
  const [goalPlan, setGoalPlan] = useState<Row | null>(null);
  const [intake, setIntake] = useState<Row | null>(null);
  const [capabilities, setCapabilities] = useState<Row[]>([]);
  const [planDirty, setPlanDirty] = useState(false);
  const [editorView, setEditorView] = useState("request");
  const [matches, setMatches] = useState<Row[] | null>(null);
  const [pendingTemplate, setPendingTemplate] = useState<Row | null>(null);
  const [templates, setTemplates] = useState<Row[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);
  const confirmation = useRef<HTMLElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [page, editorView]);
  useEffect(() => {
    if (chosen) confirmation.current?.focus();
  }, [chosen]);
  const recipeRevision = useRef(0);
  const renderedRecipeRevision = recipeRevision.current;
  const travel = recipe.scenario === "travel_planning";
  const run = useCallback(async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }, []);
  const refresh = useCallback(async () => {
    const [ms, ps, planningStatus, cs, ts, source] = await Promise.all([
      api(prefix + "/materials"),
      api(prefix + "/projects"),
      api(prefix + "/planner"),
      api(prefix + "/capabilities"),
      api(prefix + "/templates"),
      api(prefix + "/travel/source"),
    ]);
    setMaterials(ms);
    setProjects(ps);
    setPlanner(planningStatus);
    setCapabilities(cs);
    setTemplates(ts);
    setTravelSource(source);
  }, [api]);
  useEffect(() => {
    void refresh().catch((err) => setError(String(err)));
  }, [refresh]);
  const selectedVersion = version?.id;
  useEffect(() => {
    let disposed = false;
    async function tick() {
      try {
        const v = selectedVersion
          ? await api(`${prefix}/versions/${selectedVersion}`)
          : null;
        const rs =
          v?.reviews ||
          (currentJob ? await api(`${prefix}/reviews/${currentJob}`) : []);
        const hashes = rs.flatMap((r: Row) =>
          r.drafts.map((d: Row) => d.resultHash),
        );
        const ds = await Promise.all(
          hashes.map(async (hash: string) => [
            hash,
            await api(`${prefix}/drafts/${hash}`),
          ]),
        );
        if (!disposed) {
          if (v) setVersion(v);
          setReviews(rs);
          setDrafts((old) => ({ ...old, ...Object.fromEntries(ds) }));
        }
      } catch (err) {
        if (!disposed) setError(String(err));
      }
    }
    void tick();
    const timer = setInterval(() => void tick(), 2500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [api, selectedVersion, currentJob]);
  const update = (patch: Row) => {
    recipeRevision.current++;
    setMatches(null);
    const invalidates =
      [
        "scenario",
        "materialIds",
        "outputs",
        "tier",
        "groupBy",
        "travelRequest",
        "requirement",
        "budget",
        "planningMode",
      ].some((key) => key in patch) && !("planningId" in patch);
    if (invalidates) {
      setIntake(null);
      setGoalPlan(null);
      setPlanDirty(false);
      setEditorView("request");
    }
    setRecipe((old) => {
      const next = { ...old, ...patch };
      if (invalidates) next.planningId = null;
      if (!next.outputs.includes("report")) next.tier = "standard";
      return next;
    });
    setChosen(null);
    setOffers([]);
    setUnavailable([]);
    setFrontier(null);
    setUnknownHistory([]);
  };
  async function openVersion(id: string) {
    const value = await api(`${prefix}/versions/${id}`);
    setVersion(value);
    setCompare(null);
    setPage("review");
    if (value.jobId) onJob(value.jobId);
  }
  function follow(value: Row) {
    update({
      ...value.recipe,
      planningMode: value.recipe.planningMode || "manual",
      planningId: value.recipe.planningId || null,
      projectId: value.projectId,
      sourceVersion: value.id,
      forceRedo: [],
    });
    setPage("editor");
    setEditorView(value.recipe.planningId ? "plan" : "request");
    setPendingTemplate(null);
    const revision = recipeRevision.current;
    if (value.recipe.planningId)
      void api(`${prefix}/planning/${value.recipe.planningId}`)
        .then((result) => {
          if (recipeRevision.current === revision) setGoalPlan(result);
        })
        .catch((err) => setError(String(err)));
    else setGoalPlan(null);
    setNotice(
      `基于版本 ${value.number} 创建续单。原版保留，确认前会列出复用成果和新增费用。`,
    );
  }
  async function buildComparison() {
    if (
      recipe.planningMode !== "manual" &&
      (!goalPlan?.reviewedAt || planDirty)
    )
      throw new Error("请先检查并确认子任务，再比较执行方案。");
    const revision = recipeRevision.current;
    const result = await api(prefix + "/comparisons", recipe);
    if (recipeRevision.current !== revision) {
      setNotice("比较期间目标或选项发生变化，请按当前设置重新比较。");
      return;
    }
    setOffers(result.options);
    setFrontier(result.frontier || null);
    setUnknownHistory(result.excludedForNoHistory || []);
    setUnavailable(result.unavailable);
    setPage("compare");
    setChosen(null);
  }
  async function planGoal(input = recipe) {
    const revision = recipeRevision.current;
    const result = await api(prefix + "/planning/intake", {
      recipe: {
        ...input,
        title:
          input.title.trim() ||
          (input.scenario === "travel_planning"
            ? "旅游行程规划"
            : "销售数据分析"),
        planningMode: "llm",
        planningId: null,
      },
    });
    if (revision !== recipeRevision.current) {
      setNotice("需求理解期间目标发生变化，请按当前设置重新提交。");
      return;
    }
    setIntake(result);
    setGoalPlan(null);
    setPlanDirty(false);
    setOffers([]);
    setChosen(null);
    setMatches(null);
    setPendingTemplate(null);
    setEditorView("intake");
    setNotice(
      result.status === "ready"
        ? "请核对需求范围，确认后再拆成任务。"
        : "请补充必要信息，或针对能力缺口调整要求。",
    );
  }
  async function respondIntake(answers: Row, feedback: string) {
    if (!intake) return;
    const revision = recipeRevision.current;
    const result = await api(prefix + "/planning/intake", {
      recipe: intake.recipe,
      previousId: intake.id,
      answers,
      feedback,
    });
    if (revision !== recipeRevision.current) return;
    setIntake(result);
    setGoalPlan(null);
    setOffers([]);
    setChosen(null);
    setPlanDirty(false);
    setNotice(
      result.status === "ready"
        ? "需求已更新，请核对后确认。"
        : "已保留补充答案，请继续处理未明确的要求。",
    );
  }
  async function confirmIntake() {
    if (!intake) return;
    const revision = recipeRevision.current;
    const result = await api(`${prefix}/planning/intake/${intake.id}/confirm`, {
      specHash: intake.specHash,
    });
    if (revision !== recipeRevision.current) return;
    setIntake(result);
    setRecipe(result.confirmedRecipe);
    await generatePlan(result.confirmedRecipe, result.id);
  }
  async function generatePlan(input = recipe, intakeId?: string) {
    const revision = recipeRevision.current;
    setGoalPlan(null);
    setOffers([]);
    setChosen(null);
    setRecipe((old) => ({ ...old, planningId: null }));
    const result = await api(prefix + "/planning", {
      ...input,
      title:
        input.title.trim() ||
        (input.scenario === "travel_planning"
          ? "旅游行程规划"
          : "销售数据分析"),
      planningMode: "llm",
      planningId: null,
      ...(intakeId ? { intakeId } : {}),
    });
    if (recipeRevision.current !== revision) {
      setNotice("规划期间目标或选项发生变化，已保留您的修改，请重新规划。");
      return;
    }
    setGoalPlan(result);
    setPlanDirty(false);
    setEditorView("plan");
    setMatches(null);
    setPendingTemplate(null);
    if (result.status === "ready") {
      setRecipe(result.recipe);
      setNotice(
        "方案已生成。可以修改交付成果、任务内容和先后关系，确认后再执行。",
      );
    } else {
      setNotice(
        "这份目标暂不能直接执行，请按下面的问题补充或调整目标后重新规划。",
      );
    }
  }
  function acceptReviewed(result: Row) {
    if (renderedRecipeRevision !== recipeRevision.current) {
      setNotice(
        "确认期间目标或参数发生变化，已保留您的修改，请重新载入模板或规划。",
      );
      return;
    }
    update(result.recipe);
    setGoalPlan(result);
    setPlanDirty(false);
    setNotice("任务方案已确认。可以查看费用与执行方案了。");
  }
  async function adoptTemplate(item: Row, base = recipe, prepared = false) {
    const sameScenario = base.scenario === item.scenario;
    let next = {
      ...base,
      title: sameScenario && base.title.trim() ? base.title : item.name,
      scenario: item.scenario,
      requirement:
        sameScenario && base.requirement.trim()
          ? base.requirement
          : item.description,
      materialIds: sameScenario ? base.materialIds : [],
      travelRequest: sameScenario ? base.travelRequest : null,
      planningMode: "template",
      planningId: null,
      ...(!sameScenario
        ? { projectId: null, sourceVersion: null, forceRedo: [] }
        : {}),
    };
    if (next.scenario === "travel_planning" && !prepared) {
      update(next);
      setPendingTemplate(item);
      setPage("editor");
      setEditorView("request");
      const ready = await prepareTravel(next);
      if (!ready) return;
      next = ready as typeof next;
    }
    if (next.scenario !== "travel_planning" && !next.materialIds.length) {
      update(next);
      setPendingTemplate(item);
      setPage("editor");
      setEditorView("request");
      setNotice(`已选择“${item.name}”。添加材料后即可编辑这份方案。`);
      return;
    }
    const revision = recipeRevision.current;
    const result = await api(`${prefix}/templates/${item.id}/apply`, next);
    if (recipeRevision.current !== revision) {
      setNotice("采用模板期间目标发生变化，请按当前目标重新选择模板。");
      return;
    }
    update(result.recipe);
    setGoalPlan(result);
    setPlanDirty(false);
    setPage("editor");
    setEditorView("plan");
    setPendingTemplate(null);
    setNotice(
      `已载入“${item.name}”。请核对是否满足本次目标，修改不会影响原模板。`,
    );
  }
  async function prepareTravel(input: Row): Promise<Row | null> {
    const revision = recipeRevision.current;
    const result = await api(prefix + "/travel/prepare", {
      goal: input.requirement,
      answers: input.travelRequest || {},
      materialIds: input.materialIds,
      serviceBudget: input.budget,
    });
    if (revision !== recipeRevision.current) return null;
    setTravelIntent(result);
    const next = {
      ...input,
      travelRequest: result.request,
      budget: result.serviceBudget || input.budget,
    };
    setRecipe(next);
    if (result.status !== "ready") {
      setNotice(
        result.status === "needs_input"
          ? "还需要下面几项出行信息，已识别的内容无需再填。"
          : (result.issues || []).join("；"),
      );
      return null;
    }
    return next;
  }
  async function preparePlan() {
    if (!recipe.requirement.trim()) throw new Error("请写下希望完成的任务。");
    if (planner?.configured && !pendingTemplate) return planGoal();
    if (travel) {
      const next = await prepareTravel(recipe);
      if (!next) return;
      if (pendingTemplate) return adoptTemplate(pendingTemplate, next, true);
      return generatePlan(next);
    }
    if (!recipe.materialIds.length) throw new Error("请先添加一份销售数据。");
    if (pendingTemplate) return adoptTemplate(pendingTemplate);
    const revision = recipeRevision.current;
    const found = await api(
      `${prefix}/templates?q=${encodeURIComponent(recipe.requirement)}&scenario=${encodeURIComponent(recipe.scenario)}`,
    );
    if (revision !== recipeRevision.current) return;
    if (found.length) {
      setMatches(found);
      setEditorView("choose");
      return;
    }
    if (planner?.configured) return planGoal();
    setMatches([]);
    setEditorView("choose");
  }
  async function submit() {
    if (!chosen) return;
    const result = await api(prefix + "/confirm", {
      proposalId: chosen.id,
      confirmationHash: chosen.confirmationHash,
    });
    await refresh();
    await openVersion(result.versionId);
    setNotice(
      result.noCharge
        ? "直接沿用已结算成果；没有新增收费任务或费用。"
        : "已确认方案并开始执行。报告生成后会在此等待您确认。请保持服务运行。",
    );
  }
  async function download(value: Row, file: Row) {
    const response = await fetch(
      `${prefix}/versions/${value.id}/files/${file.sha256}`,
      { headers: { Authorization: "Bearer " + token } },
    );
    if (!response.ok) throw new Error("下载失败，请刷新版本后重试");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `v${value.number}-${file.name}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function decision(review: Row, action: string) {
    await api(`${prefix}/reviews/${encodeURIComponent(review.id)}`, {
      decisionId: crypto.randomUUID(),
      resultHash: review.drafts.at(-1).resultHash,
      action,
      target,
      feedback,
      revisionAction,
    });
    setFeedback("");
    setNotice(
      action === "accept"
        ? "已保存您接受的具体版本，正在验证并结算。"
        : action === "reject"
          ? "已拒绝当前报告；前置服务报酬保留，未使用余额进入退款。"
          : "修改意见已发回原执行者。本次不额外收费，稍后会生成修改稿。",
    );
    setReviews(await api(`${prefix}/reviews/${review.jobId}`));
  }
  return (
    <section className="project-workspace" aria-label="项目与成果工作区">
      <nav className="workspace-tabs" aria-label="项目操作">
        {[
          ["editor", "创建任务"],
          ["history", "我的项目"],
          ["templates", "模板库"],
          ["services", "服务管理"],
          ...(version || currentJob ? [["review", "成果验收"]] : []),
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() => {
              setPage(id);
              if (id === "history") void run("refresh", refresh);
            }}
            aria-current={
              page === id || (id === "editor" && page === "compare")
                ? "page"
                : undefined
            }
          >
            {label}
            {id === "review" &&
            reviews.some((r) => r.status === "AWAITING_REVIEW")
              ? " · 待确认"
              : ""}
          </button>
        ))}
      </nav>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="workspace-notice" role="status">
          {notice}
        </p>
      )}
      {busy && (
        <p role="status">
          {busy === "compare"
            ? "正在向在线服务估价并检查复用成果…"
            : busy === "plan"
              ? "正在准备任务方案，请稍候…"
              : "正在处理…"}
        </p>
      )}
      {page === "services" && (
        <ServiceGraph
          api={api}
          onChange={() => {
            setOffers([]);
            setChosen(null);
            setFrontier(null);
            void refresh().catch((err) => setError(String(err)));
          }}
        />
      )}
      {page === "templates" && (
        <TemplateLibrary
          api={api}
          run={run}
          busy={!!busy}
          capabilities={capabilities}
          onUse={adoptTemplate}
        />
      )}
      {page === "editor" && editorView === "request" && (
        <>
          <header className="request-heading">
            <div>
              <span className="eyebrow">从需求到任务图</span>
              <h1 ref={heading} tabIndex={-1}>
                {recipe.sourceVersion ? "继续这个项目" : "创建一个新任务"}
              </h1>
              <p>先理解目标和限制，再核对能力、拆解任务。由你确认最终方案。</p>
            </div>
            <button type="button" onClick={() => setPage("templates")}>
              <LayoutTemplate size={17} />
              从模板开始
            </button>
          </header>
          <ol className="goal-workflow" aria-label="需求到任务图流程">
            <li aria-current="step">
              <span>1</span>
              <div>
                <strong>描述需求</strong>
                <small>目标、材料与预算</small>
              </div>
            </li>
            <li>
              <span>2</span>
              <div>
                <strong>澄清与确认</strong>
                <small>补问必要信息，核对能力边界</small>
              </div>
            </li>
            <li>
              <span>3</span>
              <div>
                <strong>生成任务图</strong>
                <small>模型拆解，程序校验并查漏</small>
              </div>
            </li>
            <li>
              <span>4</span>
              <div>
                <strong>最终确认</strong>
                <small>核对需求落实后冻结 DAG</small>
              </div>
            </li>
          </ol>
          <section
            className={`planner-connection ${planner?.configured ? "is-ready" : "needs-setup"}`}
            aria-label="模型规划状态"
          >
            <div>
              <h2>
                {planner?.configured
                  ? "模型需求规划已就绪"
                  : "模型需求规划尚未启用"}
              </h2>
              <p>
                {planner?.configured
                  ? "主智能体负责拆任务，子智能体用同一个模型执行各自工作，无需分别配置。"
                  : "填写一次模型配置，主智能体与子智能体共同使用；未配置时仍可使用模板和内置业务工具。"}
              </p>
              {!planner?.configured && (
                <details>
                  <summary>怎样启用模型规划</summary>
                  <p>
                    打开项目根目录的 <code>planner.local.json</code>，填写{" "}
                    <code>endpoint</code>（完整 Chat Completions 地址）、
                    <code>model</code> 和 <code>apiKey</code>
                    。保存后点击右侧“重新检查配置”，新执行会话自动使用这份配置。
                  </p>
                </details>
              )}
            </div>
            <button
              type="button"
              disabled={!!busy}
              onClick={() =>
                void run("check-planner", async () => {
                  const result = await api(prefix + "/planner");
                  setPlanner(result);
                  setNotice(
                    result.configured
                      ? "主智能体与子智能体的共用模型已配置，现在可以提交需求。"
                      : result.message ||
                          "模型配置仍未完成，请检查接口地址、模型名和 API Key。",
                  );
                })
              }
            >
              重新检查配置
            </button>
          </section>
          <form
            className="request-layout"
            onSubmit={(event) => {
              event.preventDefault();
              void run("plan", preparePlan);
            }}
          >
            <section className="request-main">
              <fieldset className="request-fields" disabled={!!busy}>
                <legend className="sr-only">任务需求</legend>
                <div className="request-card goal-card">
                  <div className="request-card-title">
                    <Sparkles size={19} />
                    <h2>你想完成什么？</h2>
                  </div>
                  <label className="scenario-field">
                    任务类型
                    <select
                      value={recipe.scenario}
                      disabled={!!recipe.sourceVersion}
                      onChange={(e) => {
                        update({
                          scenario: e.target.value,
                          materialIds: [],
                          travelRequest: null,
                          outputs: [],
                          planningMode: "llm",
                          title: "",
                        });
                        setTravelIntent(null);
                        setPendingTemplate(null);
                      }}
                    >
                      <option value="cross_org_analysis">销售数据分析</option>
                      <option value="travel_planning">旅游行程规划</option>
                    </select>
                  </label>
                  <label className="sr-only" htmlFor="task-goal">
                    任务目标
                  </label>
                  <textarea
                    id="task-goal"
                    className="goal-input"
                    value={recipe.requirement}
                    required
                    maxLength={4000}
                    onChange={(e) => {
                      update({
                        requirement: e.target.value,
                        ...(travel ? { travelRequest: null } : {}),
                      });
                      setTravelIntent(null);
                    }}
                    placeholder={
                      travel
                        ? "例如：2026年11月1日去杭州玩三天，两人出行，旅行预算2000元，喜欢历史文化，生成完整行程方案。"
                        : "例如：分析这批销售数据，按月份汇总，比较各月销售表现，给我一份带图表的报告。"
                    }
                  />
                  {pendingTemplate && (
                    <div className="template-selection">
                      <LayoutTemplate size={17} />
                      <span>
                        使用模板：<strong>{pendingTemplate.name}</strong>
                      </span>
                      <button
                        type="button"
                        aria-label="取消使用模板"
                        onClick={() => {
                          setPendingTemplate(null);
                          update({ planningMode: "llm" });
                        }}
                      >
                        <X size={16} />
                      </button>
                    </div>
                  )}
                  <p className="field-help">
                    写下最终要拿到的成果、必须满足的限制，以及不需要的内容。模型会先整理理解，有必要时向你补问。
                  </p>
                  {travel && (
                    <div className="travel-intake">
                      <p className="field-help">
                        景点、住宿和交通资料由服务 Agent 获取，无需上传文件。
                        当前使用演示资料，非实时检索或酒店实时报价。
                        {travelSource?.destinations?.length
                          ? `支持：${travelSource.destinations.join("、")}。`
                          : ""}
                      </p>
                      {travelIntent && (
                        <>
                          {travelIntent.interpretation === "local_rules" && (
                            <p className="field-help">
                              尚未配置模型，当前识别常见出行条件并采用内置旅行模板。
                            </p>
                          )}
                          <div className="request-brief">
                            <strong>已识别的出行需求</strong>
                            <p>
                              {[
                                recipe.travelRequest?.destination,
                                recipe.travelRequest?.start_date,
                                recipe.travelRequest?.days
                                  ? `${recipe.travelRequest.days} 天`
                                  : "",
                                recipe.travelRequest?.travelers
                                  ? `${recipe.travelRequest.travelers} 人`
                                  : "",
                                recipe.travelRequest?.budget_cents
                                  ? `旅行预算 ${(recipe.travelRequest.budget_cents / 100).toFixed(2)} 元`
                                  : "",
                              ]
                                .filter(Boolean)
                                .join(" · ") || "请补充出行条件"}
                            </p>
                          </div>
                          {!!travelIntent.questions?.length && (
                            <div
                              className="compact-fields"
                              aria-label="补充出行需求"
                            >
                              {travelIntent.questions.map((question: Row) => (
                                <label key={question.field}>
                                  {question.label}
                                  <input
                                    type={
                                      question.field === "start_date"
                                        ? "date"
                                        : question.field === "destination"
                                          ? "text"
                                          : "number"
                                    }
                                    min={
                                      question.field === "budget_cents"
                                        ? "0.01"
                                        : "1"
                                    }
                                    step={
                                      question.field === "budget_cents"
                                        ? "0.01"
                                        : "1"
                                    }
                                    value={
                                      question.field === "budget_cents"
                                        ? recipe.travelRequest?.budget_cents
                                          ? recipe.travelRequest.budget_cents /
                                            100
                                          : ""
                                        : (recipe.travelRequest?.[
                                            question.field
                                          ] ?? "")
                                    }
                                    onChange={(e) => {
                                      const raw = e.target.value;
                                      const value = [
                                        "days",
                                        "travelers",
                                        "budget_cents",
                                      ].includes(question.field)
                                        ? raw
                                          ? Math.round(
                                              Number(raw) *
                                                (question.field ===
                                                "budget_cents"
                                                  ? 100
                                                  : 1),
                                            )
                                          : null
                                        : raw || null;
                                      update({
                                        travelRequest: {
                                          ...recipe.travelRequest,
                                          [question.field]: value,
                                        },
                                      });
                                    }}
                                  />
                                </label>
                              ))}
                            </div>
                          )}
                          {!!travelIntent.issues?.length && (
                            <p role="alert">{travelIntent.issues.join("；")}</p>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
                <div className="request-card">
                  <details open={!travel}>
                    <summary hidden={!travel}>补充已有资料（可选）</summary>
                    <div className="request-card-title">
                      <FolderOpen size={19} />
                      <h2>{travel ? "补充资料" : "任务材料"}</h2>
                      <span className="count-label">
                        已选 {recipe.materialIds.length} 份
                      </span>
                    </div>
                    <p className="field-help">
                      {travel
                        ? "已有资料可以自愿补充；不选择时由 Agent 查询平台演示资料库。"
                        : "添加要分析的销售数据，可选择多份一起分析。"}
                    </p>
                    <div className="material-list">
                      {materials
                        .filter((m) => m.scenario === recipe.scenario)
                        .map((m) => (
                          <label
                            className={
                              "material-option " +
                              (recipe.materialIds.includes(m.id)
                                ? "selected"
                                : "")
                            }
                            key={m.id}
                          >
                            <input
                              type={travel ? "radio" : "checkbox"}
                              name={travel ? "travel-material" : undefined}
                              checked={recipe.materialIds.includes(m.id)}
                              onChange={(e) =>
                                update({
                                  materialIds: e.target.checked
                                    ? travel
                                      ? [m.id]
                                      : [...recipe.materialIds, m.id]
                                    : recipe.materialIds.filter(
                                        (id: string) => id !== m.id,
                                      ),
                                  ...(travel && e.target.checked
                                    ? { travelRequest: m.preview.request }
                                    : {}),
                                })
                              }
                            />
                            <span>
                              <strong>{m.name}</strong>
                              <small>
                                {travel
                                  ? `${m.preview.source || "目的地资料"}${m.preview.as_of ? " · " + m.preview.as_of : ""}`
                                  : `${m.preview.rows} 行销售数据`}
                              </small>
                            </span>
                            {recipe.materialIds.includes(m.id) && (
                              <Check size={17} aria-hidden="true" />
                            )}
                          </label>
                        ))}
                      {!materials.some(
                        (m) => m.scenario === recipe.scenario,
                      ) && (
                        <p className="empty-materials">
                          {travel
                            ? "无需上传，可以直接按目标生成方案。"
                            : "还没有材料，请上传文件。"}
                        </p>
                      )}
                    </div>
                    {travel && !!recipe.materialIds.length && (
                      <button
                        type="button"
                        onClick={() => update({ materialIds: [] })}
                      >
                        取消补充资料，使用演示资料库
                      </button>
                    )}
                    <label className="upload-area">
                      <FileUp size={22} />
                      <span>
                        <strong>上传{travel ? "旅游资料" : "销售数据"}</strong>
                        <small>{travel ? "JSON" : "CSV"} 文件，最大 2 MB</small>
                      </span>
                      <input
                        type="file"
                        aria-label={travel ? "上传旅游资料" : "上传销售数据"}
                        accept={travel ? ".json" : ".csv"}
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          const revision = recipeRevision.current;
                          void run("upload", async () => {
                            if (file.size > 2_000_000)
                              throw new Error("文件不能超过 2 MB");
                            const material = await api(prefix + "/materials", {
                              name: file.name,
                              scenario: recipe.scenario,
                              content: await file.text(),
                            });
                            await refresh();
                            if (revision !== recipeRevision.current) {
                              setNotice(
                                "文件已保存到材料列表，请选择需要使用的材料。",
                              );
                              return;
                            }
                            update({
                              materialIds: travel
                                ? [material.id]
                                : [
                                    ...new Set([
                                      ...recipe.materialIds,
                                      material.id,
                                    ]),
                                  ],
                              ...(travel
                                ? { travelRequest: material.preview.request }
                                : {}),
                            });
                          });
                          e.target.value = "";
                        }}
                      />
                    </label>
                    <details className="format-help">
                      <summary>文件格式要求</summary>
                      <p>
                        {travel
                          ? "上传包含 request、attractions、hotels 的旅游 JSON 资料；目的地与出行条件可在下方调整。"
                          : "CSV 需包含 organization、order_id、date、category、quantity、unit_price_cents 列。金额以分计；UTF-8 编码；类别为 book、food、tool；日期格式 YYYY-MM-DD，最多 20000 行。"}
                      </p>
                    </details>
                    {travel &&
                      recipe.travelRequest &&
                      !!recipe.materialIds.length && (
                        <details>
                          <summary>调整出行条件</summary>
                          <div className="compact-fields">
                            {[
                              ["destination", "目的地", "text"],
                              ["start_date", "出发日期", "date"],
                              ["days", "出行天数", "number"],
                              ["travelers", "出行人数", "number"],
                              ["day_start", "每日开始", "time"],
                              ["day_end", "每日结束", "time"],
                            ].map(([key, label, type]) => (
                              <label key={key}>
                                {label}
                                <input
                                  type={type}
                                  value={recipe.travelRequest[key]}
                                  min={type === "number" ? 1 : undefined}
                                  max={key === "days" ? 7 : undefined}
                                  onChange={(e) =>
                                    update({
                                      travelRequest: {
                                        ...recipe.travelRequest,
                                        [key]:
                                          type === "number"
                                            ? Number(e.target.value)
                                            : e.target.value,
                                      },
                                    })
                                  }
                                />
                              </label>
                            ))}
                            <label>
                              行程预算（元）
                              <input
                                type="number"
                                min="0"
                                step="0.01"
                                value={recipe.travelRequest.budget_cents / 100}
                                onChange={(e) =>
                                  update({
                                    travelRequest: {
                                      ...recipe.travelRequest,
                                      budget_cents: Math.round(
                                        Number(e.target.value) * 100,
                                      ),
                                    },
                                  })
                                }
                              />
                            </label>
                            <label>
                              兴趣偏好
                              <input
                                value={(
                                  recipe.travelRequest.interests || []
                                ).join(",")}
                                onChange={(e) =>
                                  update({
                                    travelRequest: {
                                      ...recipe.travelRequest,
                                      interests: e.target.value
                                        .split(/[,，]/)
                                        .map((v) => v.trim())
                                        .filter(Boolean),
                                    },
                                  })
                                }
                              />
                            </label>
                          </div>
                        </details>
                      )}
                  </details>
                </div>
              </fieldset>
            </section>
            <aside className="request-sidebar">
              <div className="request-card budget-card">
                <h2>服务预算</h2>
                <label>
                  最多支付（USDC）
                  <input
                    inputMode="decimal"
                    pattern="[0-9]+(\.[0-9]{1,2})?"
                    value={recipe.budget}
                    required
                    disabled={!!busy}
                    onChange={(e) => update({ budget: e.target.value })}
                  />
                </label>
                <p className="field-help">
                  用于支付 Agent 的服务报酬。此时不会扣款。
                </p>
                <details>
                  <summary>更多设置</summary>
                  <label>
                    项目名称
                    <input
                      value={recipe.title}
                      maxLength={100}
                      placeholder={travel ? "旅游行程规划" : "销售数据分析"}
                      disabled={!!busy}
                      onChange={(e) => update({ title: e.target.value })}
                    />
                  </label>
                  <label>
                    选择服务时优先考虑
                    <select
                      disabled={!!busy}
                      value={
                        recipe.preference === "balanced"
                          ? "lowest_price"
                          : recipe.preference
                      }
                      onChange={(e) => update({ preference: e.target.value })}
                    >
                      <option value="lowest_price">费用更低</option>
                      <option value="fastest">完成更快</option>
                      <option value="reliability">履约记录更好</option>
                    </select>
                  </label>
                  <label>
                    报告确认时限
                    <select
                      disabled={!!busy}
                      value={recipe.reviewSeconds}
                      onChange={(e) =>
                        update({ reviewSeconds: Number(e.target.value) })
                      }
                    >
                      <option value={600}>10 分钟</option>
                      <option value={1800}>30 分钟</option>
                      <option value={3300}>55 分钟</option>
                    </select>
                  </label>
                  {recipe.sourceVersion && (
                    <fieldset>
                      <legend>需要重新执行的工作</legend>
                      {(travel
                        ? ["attractions", "hotels", "route", "report"]
                        : ["clean", "validate", "statistics", "report"]
                      ).map((key) => (
                        <label className="check-row" key={key}>
                          <input
                            type="checkbox"
                            checked={recipe.forceRedo.includes(key)}
                            onChange={(e) =>
                              update({
                                forceRedo: e.target.checked
                                  ? [...recipe.forceRedo, key]
                                  : recipe.forceRedo.filter(
                                      (v: string) => v !== key,
                                    ),
                              })
                            }
                          />
                          {labels[key]}
                        </label>
                      ))}
                    </fieldset>
                  )}
                </details>
                <button
                  className="primary request-submit"
                  type="submit"
                  disabled={!!busy}
                >
                  {busy === "plan"
                    ? "正在准备方案…"
                    : pendingTemplate
                      ? "继续编辑方案"
                      : "生成任务方案"}
                  <ArrowRight size={17} />
                </button>
                {!planner?.configured && planner && (
                  <p className="field-help">
                    {travel
                      ? "可直接识别常见出行条件并使用内置旅行方案；接入模型后支持更灵活的目标理解。"
                      : "可直接使用已有模板。按新目标生成方案需配置模型接口。"}
                  </p>
                )}
                {planner?.configured && (
                  <p className="field-help">
                    下一步先理解并澄清需求，不会直接执行或扣款。
                  </p>
                )}
                {goalPlan && (
                  <button
                    type="button"
                    className="text-action"
                    onClick={() => setEditorView("plan")}
                  >
                    返回当前方案
                  </button>
                )}
              </div>
              <div className="template-shortcuts">
                <h3>从常用模板开始</h3>
                {templates
                  .filter((t) => t.builtin)
                  .map((t) => (
                    <button
                      type="button"
                      key={t.id}
                      disabled={!!busy}
                      onClick={() =>
                        void run("use-template", () => adoptTemplate(t))
                      }
                    >
                      <LayoutTemplate size={18} />
                      <span>
                        <strong>{t.name}</strong>
                        <small>{t.tasks.length} 项工作 · 可按需修改</small>
                      </span>
                      <ArrowRight size={16} />
                    </button>
                  ))}
              </div>
              {recipe.sourceVersion && (
                <button
                  type="button"
                  onClick={() => {
                    update({
                      ...initial,
                      projectId: null,
                      sourceVersion: null,
                    });
                    setPendingTemplate(null);
                    setGoalPlan(null);
                  }}
                >
                  另建一个项目
                </button>
              )}
            </aside>
          </form>
        </>
      )}
      {page === "editor" && editorView === "choose" && (
        <section className="plan-options">
          <button
            type="button"
            className="text-action"
            onClick={() => setEditorView("request")}
          >
            <ArrowLeft size={17} />
            修改任务需求
          </button>
          <header className="request-heading">
            <div>
              <h1 ref={heading} tabIndex={-1}>
                {matches?.length ? "有可复用的任务方案" : "为这个目标创建方案"}
              </h1>
              <p>
                {matches?.length
                  ? "选择适合的模板后，可以修改每项工作和最终成果。"
                  : "没有找到相近的模板。你可以生成新方案，或到模板库中选择。"}
              </p>
            </div>
          </header>
          <div className="request-brief">
            <strong>本次目标</strong>
            <p>{recipe.requirement}</p>
            <span>
              {recipe.materialIds.length} 份材料 · 预算 {price(recipe.budget)}{" "}
              USDC
            </span>
          </div>
          <div className="template-cards">
            {matches?.map((item) => (
              <article className="request-card" key={item.id}>
                <span className="subtle-badge">
                  {item.builtin ? "内置模板" : "我的模板"}
                </span>
                <h2>{item.name}</h2>
                <p>{item.description}</p>
                <div className="output-chips">
                  {item.outputs.map((key: string) => (
                    <span key={key}>{labels[key] || key}</span>
                  ))}
                </div>
                <details>
                  <summary>包含 {item.tasks.length} 项工作</summary>
                  {item.tasks.map((task: Row) => (
                    <p key={task.key}>{task.title}</p>
                  ))}
                </details>
                <button
                  className="primary"
                  type="button"
                  disabled={!!busy}
                  onClick={() =>
                    void run("use-template", () => adoptTemplate(item))
                  }
                >
                  使用并编辑
                  <ArrowRight size={16} />
                </button>
              </article>
            ))}
          </div>
          <div className="alternative-action">
            <div>
              <h3>想要不同的方案？</h3>
              <p>
                {planner?.configured
                  ? "让 Agent 按本次目标重新生成，再由你确认。"
                  : "当前尚未配置模型接口，可以先使用并修改模板。"}
              </p>
            </div>
            <button
              type="button"
              disabled={!!busy || !planner?.configured}
              onClick={() => void run("plan", planGoal)}
            >
              按目标生成新方案
            </button>
            <button type="button" onClick={() => setPage("templates")}>
              浏览模板库
            </button>
          </div>
        </section>
      )}
      {page === "editor" && editorView === "plan" && (
        <section className="plan-workbench">
          <button
            type="button"
            className="text-action"
            onClick={() => setEditorView("request")}
          >
            <ArrowLeft size={17} />
            编辑目标与材料
          </button>
          {intake && (
            <button
              type="button"
              className="text-action"
              disabled={!!busy}
              onClick={() => setEditorView("intake")}
            >
              返回需求澄清与调整
            </button>
          )}
          <header className="request-heading">
            <div>
              <h1 ref={heading} tabIndex={-1}>
                编辑任务方案
              </h1>
              <p>核对交付成果和任务安排，确认后再查看费用。</p>
            </div>
            <span className="subtle-badge">
              预算 {price(recipe.budget)} USDC
            </span>
          </header>
          {goalPlan?.status === "ready" && goalPlan.draft ? (
            <>
              <PlanReview
                key={goalPlan.id}
                plan={goalPlan}
                recipe={recipe}
                capabilities={capabilities}
                api={api}
                run={run}
                busy={!!busy}
                onReviewed={acceptReviewed}
                onDirty={(draft) => {
                  setPlanDirty(true);
                  setGoalPlan((old) =>
                    old ? { ...old, draft, reviewedAt: null } : old,
                  );
                  setChosen(null);
                  setOffers([]);
                }}
              />
              <footer className="plan-footer">
                <div>
                  <strong>
                    {goalPlan?.reviewedAt && !planDirty
                      ? "任务方案已确认"
                      : "请先确认这份任务方案"}
                  </strong>
                  <p>
                    {goalPlan?.reviewedAt && !planDirty
                      ? "下一步查看价格和预计耗时，确认托管后才开始执行。"
                      : "修改交付或任务后，需要重新确认。"}
                  </p>
                </div>
                <button
                  className="primary"
                  type="button"
                  disabled={!!busy || !goalPlan?.reviewedAt || planDirty}
                  onClick={() => void run("compare", buildComparison)}
                >
                  查看执行方案
                  <ArrowRight size={17} />
                </button>
              </footer>
            </>
          ) : (
            <div className="request-card">
              <h2>还需要补充一些信息</h2>
              <p>{goalPlan?.summary || "请先填写任务目标并生成方案。"}</p>
              {goalPlan?.questions?.map((q: string, i: number) => (
                <p role="alert" key={i}>
                  {q}
                </p>
              ))}
              <button type="button" onClick={() => setEditorView("request")}>
                返回补充
              </button>
            </div>
          )}
        </section>
      )}
      {page === "editor" && editorView === "intake" && intake && (
        <section className="plan-workbench">
          <button
            type="button"
            className="text-action"
            disabled={!!busy}
            onClick={() => setEditorView("request")}
          >
            编辑目标与材料
          </button>
          <header className="request-heading">
            <div>
              <h1 ref={heading} tabIndex={-1}>
                理解需求与核对能力
              </h1>
              <p>明确要交付什么、必须满足哪些条件，再形成任务图。</p>
            </div>
            <span className="subtle-badge">
              服务预算 {price(recipe.budget)} USDC
            </span>
          </header>
          <RequirementReview
            key={intake.id}
            intake={intake}
            busy={!!busy}
            onRespond={(answers, feedback) =>
              run("clarify", () => respondIntake(answers, feedback))
            }
            onConfirm={() => run("generate-dag", confirmIntake)}
          />
          {goalPlan?.status === "ready" && (
            <button
              type="button"
              disabled={!!busy}
              onClick={() => setEditorView("plan")}
            >
              返回已生成的任务图
            </button>
          )}
        </section>
      )}
      {page === "compare" && (
        <section>
          <button
            type="button"
            className="text-action"
            onClick={() => {
              setPage("editor");
              setEditorView("plan");
            }}
          >
            <ArrowLeft size={17} />
            返回任务方案
          </button>
          <h1 ref={heading} tabIndex={-1}>
            选择执行方案
          </h1>
          <p>
            交付内容相同，比较费用、耗时和履约表现。商家在每项任务可以开始时确定，实际费用不超过约定预算。
          </p>
          <details>
            <summary>估价与履约记录说明</summary>
            {frontier && (
              <p>
                检查了 {frontier.candidateCombinations} 种商家组合，其中{" "}
                {frontier.feasibleCount} 种符合预算；完整前沿有{" "}
                {frontier.frontierCount} 种，当前展示 {offers.length} 种
                {frontier.truncated
                  ? "代表方案，保留费用、速度、成功率三个极端并兼顾分布"
                  : ""}
                。
              </p>
            )}
            {!!unknownHistory.length && (
              <p className="hint">
                以下服务缺少近期履约记录，暂未参与三指标比较：
                {unknownHistory.join("、")}
              </p>
            )}
            {!!offers.length && (
              <p className="hint">
                价格和耗时来自商家估价，耗时考虑等待关系及同一商家的串行执行。成功率是历史样本的估算，不是履约保证；重试、其他项目占用和人工验收可能增加耗时。
              </p>
            )}
          </details>
          {!!offers.length && (
            <details className="workspace-panel">
              <summary>查看已确认的任务与交付</summary>
              {offers[0].proposal.planning && (
                <>
                  <h3>您已确认的任务图</h3>
                  <p>{offers[0].proposal.planning.summary}</p>
                  <PlanningGraph tasks={offers[0].proposal.planning.tasks} />
                </>
              )}
              <p>
                本次交付：
                {offers[0].proposal.recipe.outputs
                  .map((key: string) => labels[key])
                  .join("、")}
              </p>
              <p>交付文件：{offers[0].proposal.deliverables.join("、")}</p>
            </details>
          )}
          {offers.length === 1 && <p role="status">当前有 1 个可选方案。</p>}
          {unavailable.map((item, index) => (
            <p role="status" key={index}>
              {item.preferences
                .map((key: string) => preferences[key])
                .join("、")}
              ：{item.reason}
            </p>
          ))}
          {!offers.length && (
            <p>
              {unavailable.length
                ? "当前没有满足预算与交付要求的方案，请返回编辑调整。交付内容未被自动修改。"
                : "请先创建并确认任务方案。"}
            </p>
          )}
          <div className="tier-grid">
            {offers.map((offer) => (
              <article
                key={offer.proposal.id}
                className={
                  "workspace-panel " +
                  (chosen?.id === offer.proposal?.id ? "chosen" : "")
                }
              >
                <h3>方案 {offer.rank}</h3>
                <>
                  <strong className="price">
                    {price(offer.proposal.estimatedPrice)} USDC
                  </strong>
                  <p>整份任务预计 {offer.proposal.estimatedSeconds} 秒</p>
                  <p>
                    <b>
                      估算整单成功率{" "}
                      {offer.proposal.pareto.estimatedSuccessPercent}%
                    </b>
                  </p>
                  {!!offer.proposal.work.length && (
                    <details>
                      <summary>查看各项服务估价</summary>
                      {offer.proposal.work.map((w: Row) => (
                        <div key={w.key}>
                          <p>
                            {w.title} · 参考商家：{w.estimatedProvider}
                            <br />
                            {price(w.estimatedPrice)} USDC ·{" "}
                            {w.estimatedSeconds} 秒
                          </p>
                          <p className="hint">
                            {w.history.window
                              ? `最近 ${w.history.window} 次，成功 ${w.history.settled} 次，成功率 ${((100 * w.history.settled) / w.history.window).toFixed(2)}%`
                              : "暂无历史履约记录"}{" "}
                            · {evidenceLabels[w.history.source]}
                          </p>
                        </div>
                      ))}
                    </details>
                  )}
                  {!offer.proposal.work.length && (
                    <p>全部沿用已结算成果，无需新增执行。</p>
                  )}
                  <button
                    disabled={!!busy}
                    onClick={() => setChosen(offer.proposal)}
                  >
                    {chosen?.id === offer.proposal.id ? "已选择" : "选择此方案"}
                  </button>
                </>
              </article>
            ))}
          </div>
          {chosen && (
            <section
              ref={confirmation}
              tabIndex={-1}
              className="workspace-panel confirmation"
            >
              <h3>确认这份方案后执行</h3>
              <p>
                最终交付：
                {chosen.recipe.outputs
                  .map((key: string) => labels[key])
                  .join("、")}{" "}
                · {preferences[chosen.recipe.preference]}
              </p>
              {chosen.recipe.requirement && (
                <p>原文字需求：{chosen.recipe.requirement}</p>
              )}
              {!!chosen.overrides?.length && (
                <p className="workspace-notice">
                  执行以您最终选择的交付、报告文件形式、服务偏好和预算为准。
                </p>
              )}
              <p>材料：{chosen.materials.map((m: Row) => m.name).join("、")}</p>
              <p>
                预计新增费用：<b>{price(chosen.estimatedPrice)} USDC</b>
                ；预算上限：
                {price(chosen.budget)} USDC。
              </p>
              <p>报告需人工接受才付款；最多免费返工两次，到期不自动接受。</p>
              <div className="workspace-columns">
                <div>
                  <h4>本次执行</h4>
                  {chosen.work.map((w: Row) => (
                    <p key={w.key}>
                      {w.title} · {price(w.estimatedPrice)} USDC
                    </p>
                  ))}
                </div>
                <div>
                  <h4>直接复用</h4>
                  {chosen.reused.map((w: Row) => (
                    <p key={w.key}>
                      {w.title}
                      <br />
                      <small>{w.reason}</small>
                    </p>
                  ))}
                  {!chosen.reused.length && <p>无</p>}
                </div>
              </div>
              <p>
                确认后托管预算。每项工作就绪时选择可用商家，费用受约定上限约束；剩余资金在任务结束后可退款。
              </p>
              <button
                className="primary"
                disabled={!!busy}
                onClick={() => void run("confirm", submit)}
              >
                {chosen.noCharge
                  ? "查看已有成果（不收费）"
                  : "确认方案并托管预算"}
              </button>
              <button disabled={!!busy} onClick={() => setPage("editor")}>
                返回修改
              </button>
            </section>
          )}
        </section>
      )}
      {page === "history" && (
        <section>
          <h2>历史版本与续单</h2>
          {!projects.length && (
            <p>确认第一份方案后，这里会保存项目和每个版本。</p>
          )}
          {projects.map((project) => (
            <article className="workspace-panel" key={project.id}>
              <h3>{project.title}</h3>
              {project.versions.map((v: Row) => (
                <div className="version-row" key={v.id}>
                  <span>
                    <b>版本 {v.number}</b> ·{" "}
                    {v.recipe.outputs
                      .map((key: string) => labels[key])
                      .join("、")}
                    <small>
                      {new Date(v.createdAt * 1000).toLocaleString()} · 估价{" "}
                      {price(v.estimatedPrice)} USDC
                    </small>
                  </span>
                  <button
                    onClick={() => void run("open", () => openVersion(v.id))}
                  >
                    打开与下载
                  </button>
                  <button
                    onClick={() =>
                      void run("follow", async () =>
                        follow(await api(`${prefix}/versions/${v.id}`)),
                      )
                    }
                  >
                    基于此版本继续
                  </button>
                </div>
              ))}
            </article>
          ))}
        </section>
      )}
      {page === "review" && (
        <section>
          <h2>
            {version
              ? `${version.recipe.title} · 版本 ${version.number}`
              : "成果预览与修改"}
          </h2>
          {version && (
            <>
              <div className="workspace-panel">
                <p>
                  状态：{statuses[version.status] || version.status}
                  ；预计新增服务费：{price(version.estimatedPrice)} USDC。
                </p>
                <p>
                  材料：{version.materials.map((m: Row) => m.name).join("、")}
                  。交付：{version.deliverables.join("、")}
                </p>
                {version.chain && (
                  <p>
                    已结算报酬：{money(version.chain.settledAmount)} USDC ·
                    已退款：
                    {money(version.chain.refunded)} USDC · 可退余额：
                    {money(version.chain.refundCredit)} USDC
                  </p>
                )}
                <div className="workspace-actions">
                  <button onClick={() => follow(version)}>
                    基于此版本继续
                  </button>
                  {version.jobId &&
                    ["SUCCEEDED", "PARTIAL_FAILED"].includes(
                      version.status,
                    ) && (
                      <button
                        disabled={!!busy}
                        onClick={() =>
                          void run("claim", async () => {
                            await api(`/api/jobs/${version.jobId}/claims`, {});
                            setNotice("已执行服务提款和剩余预算退款。");
                          })
                        }
                      >
                        完成提款与退款
                      </button>
                    )}
                  <button
                    onClick={() => {
                      if (version.jobId) onJob(version.jobId);
                    }}
                  >
                    查看当前任务记录
                  </button>
                </div>
                {version.tasks?.map((task: Row) => (
                  <p key={task.key}>
                    {task.title} ·{" "}
                    {task.checkpoint.selected
                      ? `实际执行者 ${task.checkpoint.selected.agentId} · 成交 ${money(task.checkpoint.selected.quote.price)} USDC`
                      : "等待服务成交"}{" "}
                    ·{" "}
                    {statuses[task.checkpoint.status] ||
                      task.checkpoint.status ||
                      "等待依赖"}
                  </p>
                ))}
                <h3>此版本已结算成果</h3>
                <div className="workspace-actions">
                  {version.files.map((file: Row) => (
                    <button
                      key={file.sha256}
                      onClick={() =>
                        void run("download", () => download(version, file))
                      }
                    >
                      下载 v{version.number}-{file.name}
                    </button>
                  ))}
                </div>
                <label>
                  与历史版本比较
                  <select
                    value={compare?.id || ""}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (!id) {
                        setCompare(null);
                        return;
                      }
                      void run("compare-version", async () => {
                        const other = await api(`${prefix}/versions/${id}`);
                        setCompare(other);
                        const hashes = other.reviews.flatMap((r: Row) =>
                          r.drafts.map((d: Row) => d.resultHash),
                        );
                        const pairs = await Promise.all(
                          hashes.map(async (hash: string) => [
                            hash,
                            await api(`${prefix}/drafts/${hash}`),
                          ]),
                        );
                        setDrafts((old) => ({
                          ...old,
                          ...Object.fromEntries(pairs),
                        }));
                      });
                    }}
                  >
                    <option value="">选择旧版</option>
                    {projects
                      .find((p) => p.id === version.projectId)
                      ?.versions.filter((v: Row) => v.id !== version.id)
                      .map((v: Row) => (
                        <option key={v.id} value={v.id}>
                          版本 {v.number}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
              {compare && (
                <div className="workspace-columns">
                  {[compare, version].map((v) => (
                    <section className="workspace-panel" key={v.id}>
                      <h3>版本 {v.number}</h3>
                      <p>
                        材料：{v.materials.map((m: Row) => m.name).join("、")}
                      </p>
                      <p>
                        要求：{v.recipe.requirement || "按已选选项执行"} · 分组{" "}
                        {groupLabels[v.recipe.groupBy]} ·{" "}
                        {v.recipe.tier === "detailed"
                          ? "含网页报告与来源对照表"
                          : "所选交付"}
                      </p>
                      <p>交付：{v.deliverables.join("、")}</p>
                      {v.reviews.map((r: Row) => (
                        <Draft
                          key={r.id}
                          value={
                            drafts[r.acceptedHash || r.drafts.at(-1).resultHash]
                          }
                        />
                      ))}
                    </section>
                  ))}
                </div>
              )}
            </>
          )}
          {!reviews.length && (
            <p>
              {version
                ? "任务仍在执行，或本次没有报告交付。报告通过系统检查后会在这里等待确认。"
                : "请从历史版本打开一个项目，或先创建任务方案。"}
            </p>
          )}
          {reviews.map((review) => {
            const index = selectedDraft[review.id] ?? review.drafts.length - 1;
            const hash =
              review.drafts[Math.min(index, review.drafts.length - 1)]
                .resultHash;
            const latest = index === review.drafts.length - 1;
            const canDecide =
              review.status === "AWAITING_REVIEW" &&
              latest &&
              Date.now() < review.deadline * 1000;
            const isTravel =
              version?.recipe.scenario === "travel_planning" ||
              !!drafts[hash]?.files.some(
                (f: Row) => f.name === "travel-report.md",
              );
            const targets = isTravel
              ? ["需求与资料", "住宿", "每日行程", "费用", "限制与来源"]
              : [
                  "数据与质量",
                  "统计结果",
                  "结论与限制",
                  "来源",
                  ...(drafts[hash]?.files.some(
                    (f: Row) => f.name === "revenue.svg",
                  )
                    ? ["revenue.svg"]
                    : []),
                ];
            return (
              <article className="workspace-panel" key={review.id}>
                <h3>
                  {statuses[review.status]} · {review.provider}
                </h3>
                <p>
                  确认截止：{new Date(review.deadline * 1000).toLocaleString()}{" "}
                  · 剩余免费返工 {2 - review.changes.length} 次 · 报告成交费{" "}
                  {money(review.price)} USDC。
                </p>
                <label>
                  查看草稿
                  <select
                    value={index}
                    onChange={(e) =>
                      setSelectedDraft((old) => ({
                        ...old,
                        [review.id]: Number(e.target.value),
                      }))
                    }
                  >
                    {review.drafts.map((d: Row, i: number) => (
                      <option key={d.resultHash} value={i}>
                        {i === 0 ? "初稿" : `第 ${i} 次修改稿`}
                        {d.resultHash === review.acceptedHash
                          ? " · 已接受"
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <p>
                  系统检查：
                  {review.drafts[index].checks
                    .map((check: string) => checkLabels[check] || check)
                    .join(" · ")}
                </p>
                <Draft value={drafts[hash]} />
                <details>
                  <summary>修改意见与用户决定（完整记录）</summary>
                  <pre>
                    {previewData({
                      changes: review.changes,
                      decisions: review.decisions,
                    })}
                  </pre>
                </details>
                {canDecide && (
                  <div className="review-controls">
                    <h4>对当前最新版作出决定</h4>
                    <div className="workspace-actions">
                      <button
                        className="primary"
                        disabled={!!busy}
                        onClick={() =>
                          void run("accept", () => decision(review, "accept"))
                        }
                      >
                        接受此版并结算
                      </button>
                      <button
                        className="danger"
                        disabled={!!busy}
                        onClick={() => {
                          if (
                            window.confirm(
                              "拒绝并结束当前报告？已结算的前置工作费用会保留，未使用余额可退款。",
                            )
                          )
                            void run("reject", () =>
                              decision(review, "reject"),
                            );
                        }}
                      >
                        拒绝并结束
                      </button>
                    </div>
                    {review.changes.length < 2 && (
                      <fieldset>
                        <legend>要求原执行者免费修改</legend>
                        <label>
                          具体位置
                          <select
                            value={targets.includes(target) ? target : ""}
                            onChange={(e) => setTarget(e.target.value)}
                          >
                            <option value="" disabled>
                              选择章节或图表
                            </option>
                            {targets.map((name) => (
                              <option key={name}>{name}</option>
                            ))}
                          </select>
                        </label>
                        <label>
                          修改方式
                          <select
                            value={revisionAction}
                            onChange={(e) => setRevisionAction(e.target.value)}
                          >
                            <option value="shorten">压缩重复说明</option>
                            <option value="clarify">
                              直接说明主要发现（销售结论）
                            </option>
                            <option value="costs">
                              补充费用构成解释（行程费用）
                            </option>
                            <option value="chronological">
                              按每日时间顺序展示（每日行程）
                            </option>
                            <option value="chart_labels">
                              放大文字并提升图表对比度
                            </option>
                          </select>
                        </label>
                        <label>
                          修改意见
                          <textarea
                            value={feedback}
                            maxLength={1500}
                            onChange={(e) => setFeedback(e.target.value)}
                            placeholder="描述这个位置的问题；修改方式会实际调整选中的内容。"
                          />
                        </label>
                        <button
                          disabled={
                            !!busy ||
                            !feedback.trim() ||
                            !targets.includes(target)
                          }
                          onClick={() =>
                            void run("revise", () => decision(review, "revise"))
                          }
                        >
                          提交修改意见（不加价）
                        </button>
                        <p className="hint">
                          新材料、新指标、改变天数或另一语言的完整报告，请使用“基于此版本继续”。
                        </p>
                      </fieldset>
                    )}
                  </div>
                )}
                {!latest && (
                  <p>正在查看历史草稿。请切回最新版后再接受或要求修改。</p>
                )}
              </article>
            );
          })}
        </section>
      )}
    </section>
  );
}
