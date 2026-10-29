import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  type Edge,
  type Node,
} from "@xyflow/react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronRight,
  Coins,
  Copy,
  FileCheck2,
  GitBranch,
  Layers3,
  LayoutTemplate,
  Network,
  RefreshCw,
  ShieldCheck,
  Square,
  Wallet,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import { TaskList, TaskResults, taskTitle } from "./TaskCollection";
import DesignWorkspace from "./DesignWorkspace";
import ServiceGraph from "./ServiceGraph";
import { ReviewInbox } from "./Deliverables";
import RefundClaim from "./RefundClaim";
import { Collaboration } from "./ExecutionCheck";
import { amount } from "./amount";
import BackButton from "./BackButton";

type AnyRecord = Record<string, any>;
type Task = {
  taskId: number;
  key: string;
  title: string;
  dependencies: string[];
  onchain: AnyRecord;
  checkpoint: AnyRecord;
};
type Job = {
  jobId: string;
  scenario: string;
  goal: string;
  status: string;
  lastError?: string;
  planningMode: string;
  chain: AnyRecord;
  tasks: Task[];
  domain: AnyRecord;
  token: string;
  createdAt: number;
  fundedAt?: number;
  finishedAt?: number;
};
type Agent = {
  agentId: string;
  endpoint: string;
  wallet: string;
  skills: string[];
  status: string;
  bindingVersion: number;
};
const states: Record<string, string> = {
  ACTIVE: "执行中",
  FUNDING: "托管确认中",
  SUCCEEDED: "已完成",
  PARTIAL_FAILED: "部分失败",
  INTERRUPTED: "已中断",
  BIDDING: "询价中",
  WAITING_AGENT: "等待商家空闲",
  WAITING_INPUT: "等待服务补充信息",
  WAITING_DEPENDENCIES: "等待前置成果",
  ASSIGNING: "委派确认中",
  RUNNING: "执行中",
  VERIFYING: "验证中",
  SETTLING: "结算确认中",
  SETTLED: "已结算",
  CANCELED: "已取消",
  RECOVERING: "等待旧轮次失效",
  REASSIGNING: "重新委派",
  AWAITING_REVIEW: "等待用户确认",
  REWORKING: "正在修改报告",
  ACCEPTED: "报告已经接受",
};
const chainStates = ["等待依赖", "已委派", "已结算", "已取消"];
const short = (value?: string) =>
  value ? value.slice(0, 8) + "…" + value.slice(-6) : "—";
function trapDialogTab(event: React.KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== "Tab") return;
  const elements = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]',
    ),
  );
  const first = elements[0],
    last = elements.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
const taskStatus = (t: Task) =>
  t.onchain.state >= 2
    ? chainStates[t.onchain.state]
    : states[t.checkpoint.status] || chainStates[t.onchain.state];
function Badge({ value }: { value: string }) {
  return (
    <span
      className={
        "badge " +
        (["SUCCEEDED", "SETTLED", "ONLINE"].includes(value)
          ? "good"
          : ["PARTIAL_FAILED", "INTERRUPTED", "OFFLINE", "CANCELED"].includes(
                value,
              )
            ? "warn"
            : "")
      }
    >
      <span className="status-dot" />
      {states[value] ||
        ({ ONLINE: "在线", OFFLINE: "离线" } as Record<string, string>)[
          value
        ] ||
        value}
    </span>
  );
}

function App() {
  const [token, setToken] = useState(
    sessionStorage.getItem("agentflow-token") || "",
  );
  const [draftToken, setDraftToken] = useState(""),
    [connected, setConnected] = useState(false);
  const [config, setConfig] = useState<AnyRecord>({}),
    [jobs, setJobs] = useState<AnyRecord[]>([]);
  const [jobId, setJobId] = useState(
    new URLSearchParams(location.search).get("job") || "",
  );
  const [job, setJob] = useState<Job | null>(null),
    [agents, setAgents] = useState<Agent[]>([]);
  const [quotes, setQuotes] = useState<AnyRecord[]>([]),
    [events, setEvents] = useState<AnyRecord[]>([]);
  const [artifacts, setArtifacts] = useState<AnyRecord[]>([]),
    [ledger, setLedger] = useState<AnyRecord[]>([]);
  const [error, setError] = useState("");
  const [connectionErrors, setConnectionErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(""),
    [tab, setTab] = useState(
      (() => {
        const params = new URLSearchParams(location.search);
        const view = params.get("view");
        return [
          "funds",
          "results",
          "evidence",
          "templates",
          "market",
          "design",
        ].includes(view || "")
          ? view!
          : params.has("job")
            ? "overview"
            : "workspace";
      })(),
    ),
    [selected, setSelected] = useState(0);
  const [copied, setCopied] = useState(""),
    [stopOpen, setStopOpen] = useState(false);
  const [streamStatus, setStreamStatus] = useState("连接中"),
    stopRef = useRef<HTMLDialogElement>(null);
  const selectedJobRef = useRef(jobId);
  const flowRef = useRef<ReactFlowInstance | null>(null);
  const dagRef = useRef<HTMLDivElement>(null);
  const executionRecords = useRef<HTMLElement>(null);
  const settlementHeading = useRef<HTMLHeadingElement>(null);
  const selectJob = useCallback((id: string) => {
    selectedJobRef.current = id;
    setJobId(id);
    setJob(null);
    setSelected(0);
    setEvents([]);
    setQuotes([]);
    setArtifacts([]);
    setLedger([]);
  }, []);
  const openSettlement = useCallback(
    (id: string) => {
      if (selectedJobRef.current !== id) selectJob(id);
      setTab("funds");
    },
    [selectJob],
  );
  useEffect(() => {
    if (tab === "funds") {
      settlementHeading.current?.scrollIntoView({ block: "start" });
      settlementHeading.current?.focus({ preventScroll: true });
    }
  }, [tab, token]);
  const api = useCallback(
    async (path: string, body?: unknown) => {
      const labels: Record<string, string> = {
        "/api/config": "工作区配置",
        "/api/jobs": "任务列表",
        "/api/agents": "服务列表",
        "/api/workspace/materials": "材料列表",
        "/api/workspace/projects": "项目列表",
        "/api/workspace/planner": "规划服务",
        "/api/workspace/capabilities": "服务能力",
        "/api/workspace/templates": "任务模板",
        "/api/workspace/travel/source": "旅游资料",
        "/api/workspace/designs": "对话方案",
        "/api/workspace/design-templates": "规划模板",
      };
      const label =
        labels[path] ||
        (path.startsWith("/api/jobs/") ? "任务数据" : "当前操作");
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      }).catch(() => {
        throw new Error("连接已断开，请确认项目启动窗口仍在运行，然后重试。");
      });
      if (!response.ok) {
        const text = await response.text();
        let detail = text;
        try {
          detail = JSON.parse(text).detail || text;
        } catch {
          /* plain text error */
        }
        if (
          response.status >= 500 &&
          (!detail || /Internal Server Error|^\s*</i.test(detail))
        ) {
          detail = `${label}暂时不可用（HTTP ${response.status}），请重新启动项目后重试。`;
        } else if (response.status === 404 && labels[path]) {
          detail = `${label}接口不存在，请停止原启动窗口，再重新启动更新后的项目。`;
        }
        throw new Error(
          response.status === 401 ? "访问令牌无效，请重新连接。" : detail,
        );
      }
      return response.json();
    },
    [token],
  );
  const refresh = useCallback(async () => {
    // Authentication must succeed, but an unavailable market must not block the workspace.
    const c = await api("/api/config");
    setConfig(c);
    setConnected(true);
    const [jobsResult, agentsResult] = await Promise.allSettled([
      api("/api/jobs"),
      api("/api/agents"),
    ]);
    const failures: string[] = [];
    if (jobsResult.status === "fulfilled") {
      const js = jobsResult.value;
      setJobs(js.slice().reverse());
      if (!selectedJobRef.current && js.length) selectJob(js.at(-1).jobId);
    } else {
      failures.push(
        jobsResult.reason instanceof Error
          ? jobsResult.reason.message
          : String(jobsResult.reason),
      );
    }
    if (agentsResult.status === "fulfilled") setAgents(agentsResult.value);
    else {
      setAgents([]); // An unreachable registry must not leave stale merchants marked online.
      failures.push(
        agentsResult.reason instanceof Error
          ? agentsResult.reason.message
          : String(agentsResult.reason),
      );
    }
    setConnectionErrors(failures);
    if (jobId && ["overview", "results", "funds"].includes(tab)) {
      const [j, q, e, a, l] = await Promise.all([
        api("/api/jobs/" + jobId),
        api(`/api/jobs/${jobId}/quotes`),
        api(`/api/jobs/${jobId}/audit`),
        api(`/api/jobs/${jobId}/artifacts`),
        api(`/api/jobs/${jobId}/settlements`),
      ]);
      // A response for the previous selection must never populate the new Job's view.
      if (selectedJobRef.current !== jobId) return;
      setJob(j);
      setQuotes(q);
      setEvents(e);
      setArtifacts(a);
      setLedger(l.events);
    }
  }, [api, jobId, selectJob, tab]);
  useEffect(() => {
    if (!token) return;
    let cancelled = false,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh();
        if (!cancelled) setError("");
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
      if (!cancelled) timer = setTimeout(poll, 4000);
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refresh, token]);
  useEffect(() => {
    const url = new URL(location.href);
    if (jobId && ["overview", "funds", "results"].includes(tab))
      url.searchParams.set("job", jobId);
    else url.searchParams.delete("job");
    if (tab === "workspace" || tab === "overview")
      url.searchParams.delete("view");
    else url.searchParams.set("view", tab);
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }, [jobId, tab]);
  useEffect(() => {
    if (!jobId || !token || !["overview", "results", "funds"].includes(tab))
      return;
    const controller = new AbortController();
    let last = 0;
    const listen = async () => {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(
            `/api/jobs/${jobId}/events?after=${last}`,
            {
              headers: { Authorization: "Bearer " + token },
              signal: controller.signal,
            },
          );
          if (!response.ok || !response.body) throw new Error("事件流不可用");
          setStreamStatus("实时连接");
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let end: number;
            while ((end = buffer.indexOf("\n\n")) >= 0) {
              const block = buffer.slice(0, end);
              buffer = buffer.slice(end + 2);
              const data = block
                .split("\n")
                .find((x) => x.startsWith("data: "));
              if (data) {
                const event = JSON.parse(data.slice(6));
                last = Math.max(last, event.seq);
                setEvents((old) =>
                  old.some((x) => x.seq === event.seq) ? old : [...old, event],
                );
              }
            }
          }
        } catch {
          if (!controller.signal.aborted) setStreamStatus("重连中，保留轮询");
        }
        if (!controller.signal.aborted)
          await new Promise((r) => setTimeout(r, 1500));
      }
    };
    void listen();
    return () => controller.abort();
  }, [jobId, token, tab]);
  useEffect(() => {
    if (stopOpen) stopRef.current?.showModal();
    else stopRef.current?.close();
  }, [stopOpen]);
  useEffect(() => {
    const element = dagRef.current;
    if (!element) return;
    const observer = new ResizeObserver(
      () => void flowRef.current?.fitView({ padding: 0.15 }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [job?.jobId, tab]);
  useEffect(() => {
    setEvents([]);
    setQuotes([]);
    setArtifacts([]);
    setLedger([]);
  }, [jobId]);
  async function action(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(value);
    setTimeout(() => setCopied(""), 1500);
  }
  const graph = useMemo(() => {
    const nodes: Node[] = [],
      edges: Edge[] = [];
    const ranks: Record<string, number> = {},
      columns: Record<number, number> = {};
    for (const t of job?.tasks || []) {
      const rank = t.dependencies.length
        ? Math.max(...t.dependencies.map((d) => ranks[d] || 0)) + 1
        : 0;
      ranks[t.key] = rank;
      const col = columns[rank] || 0;
      columns[rank] = col + 1;
      nodes.push({
        id: t.key,
        position: { x: rank * 250, y: col * 145 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className:
          "task-node " +
          (t.taskId === selected ? "is-selected " : "") +
          "state-" +
          t.onchain.state,
        data: {
          label: (
            <div>
              <div className="node-top">
                <span>T{t.taskId + 1}</span>
                <span>{taskStatus(t)}</span>
              </div>
              <strong>{t.title}</strong>
              <div className="node-bottom">
                <span>
                  {amount(
                    t.onchain.acceptedPrice || t.onchain.config.budgetCap,
                  )}{" "}
                  代币
                </span>
                <span>第 {t.onchain.attempt + 1} 轮</span>
              </div>
            </div>
          ),
        },
      });
      t.dependencies.forEach((d) =>
        edges.push({
          id: d + "-" + t.key,
          source: d,
          target: t.key,
          type: "smoothstep",
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: "#607f90", strokeWidth: 1.5 },
        }),
      );
    }
    return { nodes, edges };
  }, [job, selected]);
  const currentTask = job?.tasks[selected];
  const visibleQuotes = quotes
    .filter((q) => q.taskId === selected)
    .flatMap((q) =>
      q.candidates.map((c: AnyRecord) => ({
        ...c,
        attempt: q.attempt,
        chosen: q.selected?.agentId === c.agentId,
      })),
    );
  const nav = [
    ["workspace", "项目与成果", Layers3],
    ["templates", "模板库", LayoutTemplate],
    ["market", "服务市场", Network],
    ["funds", "资金账本", Wallet],
    ["evidence", "任务成果", FileCheck2],
  ] as const;
  const activeNav = ["design", "overview"].includes(tab)
    ? "workspace"
    : tab === "results"
      ? "evidence"
      : tab;
  const selectedSummary = jobs.find((j) => j.jobId === jobId);
  const openJob = (id: string, view = "overview") => {
    if (selectedJobRef.current !== id) selectJob(id);
    setTab(view);
  };
  if (!token || !connected)
    return (
      <div className="login">
        <div className="login-card">
          <div className="brand">
            <Layers3 /> AgentFlow <span>Pay</span>
          </div>
          <span className="eyebrow">LOCAL WORKSPACE</span>
          <h1>连接协作控制台</h1>
          <p>在启动窗口按 C 复制登录令牌，然后粘贴到此处。</p>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              try {
                const response = await fetch("/api/login", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ token: draftToken }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error || "登录失败，请重试。");
                sessionStorage.setItem("agentflow-token", result.session);
                setDraftToken("");
                setError("");
                setToken(result.session);
              } catch (err) {
                setError(String(err));
              }
            }}
          >
            <label htmlFor="token">访问令牌</label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={draftToken}
              onChange={(e) => setDraftToken(e.target.value)}
              required
            />
            <p className="hint">
              令牌登录成功后会自动更新；下次登录请重新复制。
            </p>
            <button className="primary" type="submit">
              连接工作区 <ArrowRight size={18} />
            </button>
          </form>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {token && !error && <p role="status">正在连接本地服务…</p>}
        </div>
      </div>
    );
  return (
    <div className="shell">
      <a className="skip" href="#main">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <div className="brand">
          <Layers3 />{" "}
          <span>
            AgentFlow <b>Pay</b>
          </span>
        </div>
        <div className="workspace">
          <span className="workspace-icon">A</span>
          <div>
            <strong>协作实验室</strong>
            <small>协作工作区</small>
          </div>
        </div>
        <span className="nav-label">工作区</span>
        <nav>
          {nav.map(([id, title, Icon]) => (
            <button
              key={id}
              className={activeNav === id ? "active" : ""}
              onClick={() => setTab(id)}
              aria-current={activeNav === id ? "page" : undefined}
            >
              <Icon size={19} />
              {title}
              {activeNav === id && <ChevronRight size={16} />}
            </button>
          ))}
        </nav>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <span>
            工作区 <ChevronRight size={14} />{" "}
            {nav.find((n) => n[0] === activeNav)?.[1]}
          </span>
          <div>
            <span className="local-pill">
              <span className="status-dot" />
              已连接区块链
            </span>
            <button
              className="icon-button"
              title="刷新数据"
              aria-label="刷新数据"
              onClick={() => void action("refresh", refresh)}
            >
              <RefreshCw size={17} />
            </button>
            <button
              className="identity"
              onClick={() => {
                sessionStorage.removeItem("agentflow-token");
                setToken("");
                setConnected(false);
              }}
              title="断开连接"
            >
              <span>U</span>
              {short(config.owner)}
            </button>
          </div>
        </header>
        <main id="main">
          {connectionErrors.length > 0 && (
            <div className="error" role="alert">
              {connectionErrors.join("；")}
              <button onClick={() => void action("refresh", refresh)}>
                重试连接
              </button>
            </div>
          )}
          <div hidden={!["workspace", "design", "templates"].includes(tab)}>
            <DesignWorkspace
              api={api}
              jobs={jobs}
              mode={
                tab === "templates"
                  ? "templates"
                  : tab === "design"
                    ? "chat"
                    : "home"
              }
              onOpenChat={() => setTab("design")}
              onOpenHome={() => setTab("workspace")}
              onOpenTemplates={() => setTab("templates")}
              onJob={(id) => openJob(id)}
              onResults={(id) => openJob(id, "results")}
            />
          </div>
          {tab === "evidence" && (
            <section aria-label="任务成果列表">
              <div className="page-heading">
                <div>
                  <h1>任务成果</h1>
                  <p>选择一次任务，查看最终交付和中间成果。</p>
                </div>
              </div>
              <TaskList
                jobs={jobs}
                onOpen={(id) => openJob(id, "results")}
                empty="任务发起后，会在这里按任务保存成果。"
              />
            </section>
          )}
          {!["workspace", "design", "templates", "evidence"].includes(tab) && (
            <>
              <div className="page-heading">
                <div className="page-title-block">
                  <BackButton
                    onClick={() =>
                      setTab(tab === "results" ? "evidence" : "workspace")
                    }
                  >
                    返回{tab === "results" ? "全部任务成果" : "项目与成果"}
                  </BackButton>
                  <h1 ref={settlementHeading} tabIndex={-1}>
                    {tab === "funds"
                      ? "任务结算"
                      : tab === "market"
                        ? "服务市场"
                        : taskTitle(selectedSummary || job || {})}
                  </h1>
                  {tab === "funds" && job && (
                    <p>{taskTitle(selectedSummary || job)}</p>
                  )}
                </div>
                {job && ["overview", "results"].includes(tab) && (
                  <div className="workspace-actions">
                    <Badge
                      value={selectedSummary?.reviewStatus || job.status}
                    />
                    <button
                      onClick={() =>
                        setTab(tab === "results" ? "overview" : "results")
                      }
                    >
                      {tab === "results" ? "查看执行进度" : "查看任务成果"}
                    </button>
                    <button onClick={() => openSettlement(job.jobId)}>
                      查看结算
                    </button>
                  </div>
                )}
              </div>
              {error && (
                <div className="error" role="alert">
                  {error}
                  <button onClick={() => void action("refresh", refresh)}>
                    重试
                  </button>
                </div>
              )}
              {tab === "funds" && (
                <div className="job-picker">
                  <label htmlFor="job">当前任务</label>
                  <select
                    id="job"
                    value={jobId}
                    onChange={(e) => {
                      selectJob(e.target.value);
                    }}
                  >
                    <option value="" disabled>
                      尚未创建任务
                    </option>
                    {jobs.map((j) => (
                      <option key={j.jobId} value={j.jobId}>
                        {taskTitle(j)} · {states[j.status] || j.status}
                      </option>
                    ))}
                  </select>
                  {job && <Badge value={job.status} />}
                  <span className="stream">
                    <Activity size={14} />
                    {jobId ? streamStatus : "等待任务"}
                  </span>
                </div>
              )}
              {tab === "market" ? (
                <ServiceGraph
                  api={api}
                  agents={agents}
                  onChange={() => void refresh()}
                />
              ) : !job ? (
                <section className="panel empty">
                  <GitBranch size={36} />
                  <h2>{jobId ? "正在读取任务…" : "还没有任务"}</h2>
                  <BackButton onClick={() => setTab("workspace")}>
                    返回项目与成果
                  </BackButton>
                </section>
              ) : (
                <>
                  {tab === "funds" && (
                    <div className="stats-grid">
                      {[
                        ["托管预算", job.chain.deposit, Coins],
                        [
                          tab === "funds" ? "实际服务费" : "本任务已结算报酬",
                          BigInt(job.chain.agentCredits) +
                            BigInt(job.chain.withdrawn),
                          ShieldCheck,
                        ],
                        ["已退还用户", job.chain.refunded, Wallet],
                      ].map(([title, value, Icon]: any) => (
                        <div className="metric" key={title}>
                          <div>
                            <span>{title}</span>
                            <Icon size={18} />
                          </div>
                          <strong>
                            {amount(String(value))}
                            <small>代币</small>
                          </strong>
                        </div>
                      ))}
                    </div>
                  )}
                  {(tab === "funds" ||
                    (tab === "overview" && job.chain.finalized)) && (
                    <RefundClaim
                      key={job.jobId}
                      jobId={job.jobId}
                      chain={job.chain}
                      api={api}
                      onChange={refresh}
                    />
                  )}
                  {tab === "overview" && (
                    <>
                      <ReviewInbox
                        key={`review-${job.jobId}`}
                        api={api}
                        jobId={job.jobId}
                        onChange={refresh}
                        onSettlement={openSettlement}
                      />
                      <section className="content-section panel execution-graph">
                        <h3 className="section-title">
                          任务进度 ·{" "}
                          {
                            job.tasks.filter((t) => t.onchain.state === 2)
                              .length
                          }
                          /{job.tasks.length} 项已完成
                        </h3>
                        <div className="panel-heading">
                          <span className="stream">
                            <Activity size={14} />
                            {streamStatus}
                          </span>
                          <div className="job-actions">
                            <span className="mono">{short(job.jobId)}</span>
                            <button
                              className="icon-button"
                              aria-label="复制任务 ID"
                              onClick={() => void copy(job.jobId)}
                            >
                              {copied === job.jobId ? (
                                <Check size={16} />
                              ) : (
                                <Copy size={16} />
                              )}
                            </button>
                            {!["SUCCEEDED", "PARTIAL_FAILED"].includes(
                              job.status,
                            ) && (
                              <button
                                className="danger-outline"
                                onClick={() => setStopOpen(true)}
                              >
                                <Square size={13} />
                                停止新委派
                              </button>
                            )}
                          </div>
                        </div>
                        <div
                          className="dag"
                          ref={dagRef}
                          aria-label="任务依赖图"
                        >
                          <ReactFlow
                            onInit={(instance) => {
                              flowRef.current = instance;
                            }}
                            nodes={graph.nodes}
                            edges={graph.edges}
                            fitView
                            fitViewOptions={{ padding: 0.2 }}
                            nodesDraggable={false}
                            nodesConnectable={false}
                            onNodeClick={(_, n) => {
                              setSelected(
                                job.tasks.findIndex((t) => t.key === n.id),
                              );
                              if (executionRecords.current) {
                                executionRecords.current.scrollIntoView({
                                  block: "start",
                                  behavior: "smooth",
                                });
                              }
                            }}
                            colorMode="dark"
                            minZoom={0.3}
                            maxZoom={1.5}
                          >
                            <Background gap={24} size={1} />
                            <Controls showInteractive={false} />
                          </ReactFlow>
                        </div>
                        <div className="graph-legend">
                          <span>
                            <i className="legend-dot completed" />
                            已结算
                          </span>
                          <span>
                            <i className="legend-dot running" />
                            执行或确认中
                          </span>
                          <span>
                            <i className="legend-dot waiting" />
                            等待委派
                          </span>
                          <span>点击任务查看费用与执行状态</span>
                        </div>
                      </section>
                      <Collaboration
                        key={`collaboration-${job.jobId}`}
                        api={api}
                        jobId={job.jobId}
                        tasks={job.tasks}
                      />
                      <section
                        className="content-section execution-records"
                        ref={executionRecords}
                      >
                        <h3 className="section-title">报价与执行记录</h3>
                        <div className="detail-grid">
                          <section className="panel">
                            <div className="panel-heading">
                              <div>
                                <h2>节点详情</h2>
                                <p>
                                  {currentTask?.title} · 第{" "}
                                  {(currentTask?.onchain.attempt || 0) + 1} 轮
                                </p>
                              </div>
                              <span className="tag">
                                {currentTask ? taskStatus(currentTask) : "—"}
                              </span>
                            </div>
                            {currentTask && (
                              <>
                                <div className="detail-fields">
                                  <div>
                                    <span>预算上限</span>
                                    <strong>
                                      {amount(
                                        currentTask.onchain.config.budgetCap,
                                      )}{" "}
                                      代币
                                    </strong>
                                  </div>
                                  <div>
                                    <span>成交金额</span>
                                    <strong>
                                      {currentTask.onchain.state === 0
                                        ? "—"
                                        : amount(
                                            currentTask.onchain.acceptedPrice,
                                          ) + " 代币"}
                                    </strong>
                                  </div>
                                  <div>
                                    <span>当前执行者</span>
                                    <strong>
                                      {currentTask.checkpoint.selected
                                        ?.agentId || "尚未选择"}
                                    </strong>
                                  </div>
                                  <div>
                                    <span>验收结果</span>
                                    <strong>
                                      {currentTask.checkpoint.status ===
                                      "AWAITING_REVIEW"
                                        ? "报告已生成，等待您验收"
                                        : currentTask.onchain.state === 2
                                          ? "验证通过并已结算"
                                          : currentTask.checkpoint.verification
                                                ?.passed
                                            ? "系统检查通过"
                                            : "尚未完成检查"}
                                    </strong>
                                  </div>
                                </div>
                                <p className="hint">
                                  {currentTask.checkpoint.reason || ""}
                                </p>
                              </>
                            )}
                            <div className="table-wrap">
                              <table>
                                <thead>
                                  <tr>
                                    <th>候选服务</th>
                                    <th>报价</th>
                                    <th>选择结果</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {visibleQuotes.length ? (
                                    visibleQuotes.map((q, i) => (
                                      <tr key={i}>
                                        <td>
                                          {q.agentId}
                                          <small>轮次 {q.attempt + 1}</small>
                                        </td>
                                        <td>
                                          {q.valid
                                            ? amount(q.quote.price) + " 代币"
                                            : "—"}
                                        </td>
                                        <td>
                                          {q.valid ? (
                                            q.chosen ? (
                                              <span className="text-good">
                                                已选择
                                              </span>
                                            ) : (
                                              "合格候选"
                                            )
                                          ) : (
                                            <span title={q.reason}>已拒绝</span>
                                          )}
                                        </td>
                                      </tr>
                                    ))
                                  ) : (
                                    <tr>
                                      <td colSpan={3}>节点就绪后开始询价</td>
                                    </tr>
                                  )}
                                </tbody>
                              </table>
                            </div>
                          </section>
                          <section className="panel timeline-panel">
                            <div className="panel-heading">
                              <div>
                                <h2>执行时间线</h2>
                                <p>来自服务事件与交易确认</p>
                              </div>
                              <Activity size={18} />
                            </div>
                            <div className="timeline">
                              {events
                                .slice(-10)
                                .reverse()
                                .map((e) => (
                                  <div className="timeline-item" key={e.seq}>
                                    <span className="timeline-dot" />
                                    <div>
                                      <strong>
                                        {states[e.kind] ||
                                          (
                                            {
                                              JOB_FUNDED: "托管已确认",
                                              TX_CONFIRMED: "链上交易确认",
                                              QUOTES_RECEIVED: "收到候选报价",
                                              WORKER_INTERRUPTED: "执行已中断",
                                            } as Record<string, string>
                                          )[e.kind] ||
                                          "任务状态更新"}
                                      </strong>
                                      <p>
                                        {(
                                          {
                                            create: "托管预算",
                                            assign: "委派服务",
                                            settle: "结算报酬",
                                            finalize: "结束订单",
                                            withdraw: "领取报酬",
                                            refund: "退回余额",
                                          } as AnyRecord
                                        )[
                                          String(
                                            e.payload.operation || "",
                                          ).split(":")[0]
                                        ] ||
                                          ("taskId" in e.payload
                                            ? "任务 T" + (e.payload.taskId + 1)
                                            : "任务状态更新")}
                                      </p>
                                      <time>
                                        {new Date(
                                          e.created * 1000,
                                        ).toLocaleTimeString("zh-CN")}
                                      </time>
                                    </div>
                                  </div>
                                ))}
                            </div>
                          </section>
                        </div>
                      </section>
                    </>
                  )}
                  {tab === "funds" && (
                    <section
                      className="panel settlement-panel"
                      aria-label="任务结算明细"
                    >
                      <div className="panel-heading">
                        <div>
                          <h2 aria-live="polite">
                            {job.chain.finalized ? "结算完成" : "结算进行中"}
                          </h2>
                          <p aria-live="polite">
                            {job.chain.finalized
                              ? BigInt(job.chain.refundCredit) > 0n
                                ? "本次服务费用已确认，未使用的代币通过上方按钮领取。"
                                : BigInt(job.chain.refunded) > 0n
                                  ? "本次服务费用已确认，剩余代币已退回你的钱包。"
                                  : "本次服务费用已确认，预算已全部用于服务。"
                              : "已完成的服务费用会陆续入账，任务结束后确定退款金额。此页会自动更新。"}
                          </p>
                        </div>
                      </div>
                      {!job.chain.finalized && job.lastError && (
                        <p className="inline-error" role="alert">
                          结算暂未完成：{job.lastError}
                        </p>
                      )}
                      <div className="settlement-links">
                        <button onClick={() => setTab("results")}>
                          查看任务成果
                        </button>
                        <button
                          disabled={!!busy}
                          onClick={() => void action("refresh", refresh)}
                        >
                          刷新结算
                        </button>
                      </div>
                      <div className="table-wrap">
                        <table aria-label="各项服务费用">
                          <thead>
                            <tr>
                              <th>服务内容</th>
                              <th>结算状态</th>
                              <th>已结算费用</th>
                            </tr>
                          </thead>
                          <tbody>
                            {job.tasks.map((task) => (
                              <tr key={task.taskId}>
                                <td>{task.title}</td>
                                <td>{taskStatus(task)}</td>
                                <td>
                                  {amount(
                                    Number(task.onchain.state) === 2
                                      ? task.onchain.acceptedPrice
                                      : "0",
                                  )}{" "}
                                  代币
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="ledger-balances">
                        <span>
                          任务预留 <strong>{amount(job.chain.reserved)}</strong>
                        </span>
                        <span>
                          未使用{" "}
                          <strong>{amount(job.chain.unallocated)}</strong>
                        </span>
                        <span>
                          本任务待提现{" "}
                          <strong>{amount(job.chain.agentCredits)}</strong>
                        </span>
                        <span>
                          已提现 <strong>{amount(job.chain.withdrawn)}</strong>
                        </span>
                      </div>
                      <section className="content-section settlement-transactions">
                        <h3 className="section-title">查看链上交易记录</h3>
                        <div className="table-wrap">
                          <table>
                            <thead>
                              <tr>
                                <th>链上事件</th>
                                <th>任务</th>
                                <th>金额 代币</th>
                                <th>区块</th>
                                <th>交易哈希</th>
                              </tr>
                            </thead>
                            <tbody>
                              {ledger.map((e, i) => (
                                <tr key={i}>
                                  <td>{e.event}</td>
                                  <td>
                                    {e.args.taskId !== undefined
                                      ? "T" + (e.args.taskId + 1)
                                      : "整单"}
                                  </td>
                                  <td>
                                    {amount(
                                      e.args.amount ??
                                        e.args.price ??
                                        e.args.deposit ??
                                        e.args.refund,
                                    )}
                                  </td>
                                  <td>#{e.blockNumber}</td>
                                  <td>
                                    <button
                                      className="hash-button"
                                      onClick={() =>
                                        void copy(e.transactionHash)
                                      }
                                      title={e.transactionHash}
                                    >
                                      {short(e.transactionHash)}
                                      {copied === e.transactionHash ? (
                                        <Check size={14} />
                                      ) : (
                                        <Copy size={14} />
                                      )}
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </section>
                      <p className="hint">
                        金额四舍五入显示两位小数；实际结算保留完整精度。Gas
                        使用对应链的原生币单独支付。
                      </p>
                    </section>
                  )}
                  {tab === "results" && (
                    <TaskResults
                      key={job.jobId}
                      api={api}
                      job={job}
                      artifacts={artifacts}
                      finalTaskKeys={selectedSummary?.finalTaskKeys}
                      onChange={refresh}
                      onSettlement={openSettlement}
                    />
                  )}
                </>
              )}
            </>
          )}
        </main>
      </div>
      <dialog
        ref={stopRef}
        onCancel={() => setStopOpen(false)}
        onKeyDown={trapDialogTab}
        className="stop-dialog"
        aria-labelledby="stop-title"
      >
        <h2 id="stop-title">停止后续新委派？</h2>
        <p>
          已经接受的委派保留履约窗口，已确认的报酬仍属于执行者。剩余资金在任务收尾后可退。
        </p>
        <div className="modal-actions">
          <button autoFocus onClick={() => setStopOpen(false)}>
            继续运行
          </button>
          <button
            className="danger"
            disabled={!!busy}
            onClick={() =>
              void action("stop", async () => {
                await api(`/api/jobs/${jobId}/stop`, {});
                setStopOpen(false);
              })
            }
          >
            停止新委派
          </button>
        </div>
      </dialog>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
