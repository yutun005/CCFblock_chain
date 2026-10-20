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
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  Coins,
  Copy,
  Database,
  FileCheck2,
  GitBranch,
  Layers3,
  Loader2,
  LockKeyhole,
  Network,
  Plus,
  RefreshCw,
  ShieldCheck,
  Square,
  Wallet,
  X,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import "./style.css";
import Workspace from "./Workspace";
import { amount, price, tokenUnits } from "./amount";

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
  const [createError, setCreateError] = useState("");
  const [scenario, setScenario] = useState("cross_org_analysis"),
    [preview, setPreview] = useState<AnyRecord | null>(null),
    [previewError, setPreviewError] = useState("");
  const [busy, setBusy] = useState(""),
    [tab, setTab] = useState("workspace"),
    [selected, setSelected] = useState(0);
  const [createOpen, setCreateOpen] = useState(false),
    [budget, setBudget] = useState("100"),
    [sla, setSla] = useState(30);
  const [copied, setCopied] = useState(""),
    [stopOpen, setStopOpen] = useState(false);
  const [streamStatus, setStreamStatus] = useState("连接中"),
    modalRef = useRef<HTMLDialogElement>(null),
    stopRef = useRef<HTMLDialogElement>(null);
  const selectedJobRef = useRef(jobId);
  const flowRef = useRef<ReactFlowInstance | null>(null);
  const dagRef = useRef<HTMLDivElement>(null);
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
  const api = useCallback(
    async (path: string, body?: unknown) => {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        const text = await response.text();
        let detail = text;
        try { detail = JSON.parse(text).detail || text; } catch { /* plain text error */ }
        throw new Error(
          response.status === 401 ? "访问令牌无效，请重新连接。" : detail,
        );
      }
      return response.json();
    },
    [token],
  );
  const refresh = useCallback(async () => {
    const [c, js, as] = await Promise.all([
      api("/api/config"),
      api("/api/jobs"),
      api("/api/agents"),
    ]);
    setConfig(c);
    setJobs(js.slice().reverse());
    setAgents(as);
    setConnected(true);
    if (!selectedJobRef.current && js.length) selectJob(js.at(-1).jobId);
    if (jobId) {
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
  }, [api, jobId, selectJob]);
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
    if (jobId) history.replaceState(null, "", "?job=" + jobId);
  }, [jobId]);
  useEffect(() => {
    if (!jobId || !token) return;
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
  }, [jobId, token]);
  useEffect(() => {
    if (createOpen) { setCreateError(""); modalRef.current?.showModal(); }
    else modalRef.current?.close();
  }, [createOpen]);
  useEffect(() => {
    if (!createOpen) return;
    let cancelled = false;
    setPreview(null);
    setPreviewError("");
    if (!/^\d+(\.\d{1,6})?$/.test(budget) || Number(budget) < 90) {
      setPreviewError("Agent 服务托管预算至少为 90 代币，金额最多保留六位小数。");
      return;
    }
    if (!Number.isInteger(sla) || sla < 10 || sla > 120) {
      setPreviewError("每轮结算时限请填写 10 至 120 秒的整数。");
      return;
    }
    const timer = setTimeout(() => {
      void api("/api/plans/preview", { scenario, deposit: budget, slaSeconds: sla })
        .then((value) => { if (!cancelled) setPreview(value); })
        .catch((reason) => { if (!cancelled) setPreviewError(reason instanceof Error ? reason.message : String(reason)); });
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [api, createOpen, scenario, budget, sla]);
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
    if (name === "create") setCreateError(""); else setError("");
    try {
      await fn();
      if (name !== "create") await refresh();
    } catch (e) {
      if (name === "create") setCreateError(String(e)); else setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function createJob() {
    if (!preview) return;
    await action("create", async () => {
      const r = await api("/api/jobs", {
        scenario,
        deposit: budget,
        slaSeconds: sla,
        expectedPlanHash: preview.planHash,
        requestId: crypto.randomUUID(),
      });
      selectJob(r.jobId);
      setTab("overview");
      setCreateOpen(false);
    });
  }
  async function download(ref: AnyRecord) {
    await action("download", async () => {
      const r = await fetch(`/api/jobs/${jobId}/artifacts/${ref.sha256}`, {
        headers: { Authorization: "Bearer " + token },
      });
      if (!r.ok) throw new Error("下载失败");
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = ref.name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
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
    ["overview", "协作任务", GitBranch],
    ["market", "服务市场", Network],
    ["funds", "资金账本", Wallet],
    ["evidence", "成果与证据", FileCheck2],
  ] as const;
  if (!token || !connected)
    return (
      <div className="login">
        <div className="login-card">
          <div className="brand">
            <Layers3 /> AgentFlow <span>Pay</span>
          </div>
          <span className="eyebrow">LOCAL WORKSPACE</span>
          <h1>连接协作控制台</h1>
          <p>
            使用服务管理员配置的访问令牌连接协作工作区。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              sessionStorage.setItem("agentflow-token", draftToken);
              setToken(draftToken);
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
            <p className="hint">令牌由服务管理员通过 AGENTFLOW_API_TOKEN 配置。</p>
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
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
              aria-current={tab === id ? "page" : undefined}
            >
              <Icon size={19} />
              {title}
              {tab === id && <ChevronRight size={16} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <ShieldCheck size={20} />
          <strong>DASC · 依赖感知结算</strong>
          <p>报酬由任务状态和有效凭证共同约束。</p>
          <small>EVM · Chain {config.chainId}</small>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <span>
            工作区 <ChevronRight size={14} />{" "}
            {nav.find((n) => n[0] === tab)?.[1]}
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
          <div hidden={tab !== "workspace"}>
            <Workspace api={api} token={token} currentJob={jobId} onJob={selectJob} />
          </div>
          {tab !== "workspace" && <>
          <div className="page-heading">
            <div>
              <div className="eyebrow">AUTONOMOUS COLLABORATION</div>
              <h1>{nav.find((n) => n[0] === tab)?.[1]}</h1>
              <p>从服务发现到成果交付，追踪每个节点和每笔报酬。</p>
            </div>
            <button className="primary" onClick={() => setTab("workspace")}>
              <Plus size={18} />
              创建协作任务
            </button>
          </div>
          <div className="mode-note">
            <LockKeyhole size={16} />
            <span>
              任务依赖调度 · 独立成果验证 · A2A 服务协作 · 合约托管结算
            </span>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}
              <button onClick={() => void action("refresh", refresh)}>
                重试
              </button>
            </div>
          )}
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
                  {short(j.jobId)} · {states[j.status] || j.status}
                </option>
              ))}
            </select>
            {job && <Badge value={job.status} />}
            <span className="stream">
              <Activity size={14} />
              {jobId ? streamStatus : "等待任务"}
            </span>
          </div>
          {tab === "market" ? (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>可发现的服务 Agent</h2>
                    <p>
                      {agents.filter((a) => a.status === "ONLINE").length}{" "}
                      个在线实例 · 身份经过端点签名挑战
                    </p>
                  </div>
                  <Network />
                </div>
                <div className="agent-grid">
                  {agents.map((a) => (
                    <div className="agent-card" key={a.agentId}>
                      <div className="agent-title">
                        <span className="agent-avatar">
                          <Network size={19} />
                        </span>
                        <strong>{a.agentId}</strong>
                        <Badge value={a.status} />
                      </div>
                      <p>{a.skills.join(" / ")}</p>
                      <div className="row">
                        <span>钱包</span>
                        <code title={a.wallet}>{short(a.wallet)}</code>
                      </div>
                      <div className="row">
                        <span>注册版本</span>
                        <span>v{a.bindingVersion}</span>
                      </div>
                      <small>{a.endpoint}</small>
                    </div>
                  ))}
                </div>
              </section>
            </>
          ) : !job ? (
            <section className="panel empty">
              <GitBranch size={36} />
              <h2>{jobId ? "正在读取任务…" : "创建你的第一个协作任务"}</h2>
              <p>选择销售分析或旅游规划，由服务 Agent 协作执行并按履约结算。</p>
              {!jobId && (
                <button className="primary" onClick={() => setCreateOpen(true)}>
                  查看计划并开始 <ArrowRight size={16} />
                </button>
              )}
            </section>
          ) : (
            <>
              <div className="stats-grid">
                {[
                  ["托管预算", job.chain.deposit, Coins],
                  [
                    "本任务已结算报酬",
                    BigInt(job.chain.agentCredits) + BigInt(job.chain.withdrawn),
                    ShieldCheck,
                  ],
                  ["可领取退款", job.chain.refundCredit, ArrowDownToLine],
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
              {tab === "overview" && (
                <>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <h2>{config.scenarios?.find((s: AnyRecord) => s.id === job.scenario)?.title || job.scenario}</h2>
                        <p>{config.scenarios?.find((s: AnyRecord) => s.id === job.scenario)?.description || job.goal}</p>
                      </div>
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
                    <div className="dag" ref={dagRef} aria-label="任务依赖图">
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
                        onNodeClick={(_, n) =>
                          setSelected(
                            job.tasks.findIndex((t) => t.key === n.id),
                          )
                        }
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
                      <span>点击节点查看报价与执行证据</span>
                    </div>
                  </section>
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
                                {amount(currentTask.onchain.config.budgetCap)}{" "}
                                代币
                              </strong>
                            </div>
                            <div>
                              <span>成交金额</span>
                              <strong>
                                {currentTask.onchain.state === 0
                                  ? "—"
                                  : amount(currentTask.onchain.acceptedPrice) +
                                    " 代币"}
                              </strong>
                            </div>
                            <div>
                              <span>当前执行者</span>
                              <strong>
                                {currentTask.checkpoint.selected?.agentId ||
                                  "尚未选择"}
                              </strong>
                            </div>
                            <div>
                              <span>验收结果</span>
                              <strong>
                                {currentTask.checkpoint.verification?.passed
                                  ? "客观规则通过"
                                  : "等待验证"}
                              </strong>
                            </div>
                          </div>
                          <p className="hint">
                            {currentTask.checkpoint.reason ||
                              "报价、结果和验证证明均绑定此任务及执行轮次。"}
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
                                    e.kind}
                                </strong>
                                <p>
                                  {e.payload.operation ||
                                    e.payload.reason ||
                                    ("taskId" in e.payload
                                      ? "任务 T" + (e.payload.taskId + 1)
                                      : "任务状态更新")}
                                </p>
                                <time>
                                  {new Date(
                                    e.created * 1000,
                                  ).toLocaleTimeString()}
                                </time>
                              </div>
                            </div>
                          ))}
                      </div>
                    </section>
                  </div>
                </>
              )}
              {tab === "funds" && (
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>逐任务结算账本</h2>
                      <p>结算数据来自合约任务状态；报酬按当前任务领取，已提现、待提现和退款均有独立链上账目。</p>
                    </div>
                    <button
                      disabled={!!busy || !job.chain.finalized}
                      className="primary"
                      onClick={() =>
                        void action("claims", () =>
                          api(`/api/jobs/${jobId}/claims`, {}),
                        )
                      }
                    >
                      {busy === "claims" ? (
                        <Loader2 className="spin" size={17} />
                      ) : (
                        <ArrowDownToLine size={17} />
                      )}
                      领取报酬与退款
                    </button>
                  </div>
                  <div className="ledger-balances">
                    <span>
                      任务预留 <strong>{amount(job.chain.reserved)}</strong>
                    </span>
                    <span>
                      未使用 <strong>{amount(job.chain.unallocated)}</strong>
                    </span>
                    <span>
                      本任务待提现{" "}
                      <strong>{amount(job.chain.agentCredits)}</strong>
                    </span>
                    <span>已提现 <strong>{amount(job.chain.withdrawn)}</strong></span>
                  </div>
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
                                onClick={() => void copy(e.transactionHash)}
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
                  <p className="hint">
                    金额四舍五入显示两位小数；实际结算保留完整精度。Gas 使用对应链的原生币单独支付。
                  </p>
                </section>
              )}
              {tab === "evidence" && (
                <>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <h2>成果文件</h2>
                        <p>查看各项任务的交付文件和结算状态，下载所需成果。</p>
                      </div>
                      <FileCheck2 />
                    </div>
                    {artifacts.length ? (
                      artifacts.map((d) => (
                        <div className="artifact-group" key={d.resultHash}>
                          <div>
                            <span className="tag">
                              T{d.manifest.taskId + 1} · 轮次{" "}
                              {d.manifest.attempt + 1}
                            </span>
                            <Badge
                              value={
                                job.tasks[d.manifest.taskId].onchain
                                  .resultHash === d.resultHash
                                  ? "SETTLED"
                                  : "未结算成果"
                              }
                            />
                          </div>
                          {d.manifest.artifacts.map((a: AnyRecord) => (
                            <div className="artifact" key={a.sha256}>
                              <FileCheck2 size={20} />
                              <div>
                                <strong>{a.name}</strong>
                                <small>
                                  {(a.size / 1024).toFixed(1)} KB ·{" "}
                                  {a.media_type}
                                </small>
                              </div>
                              <button
                                aria-label={"下载 " + a.name}
                                onClick={() => void download(a)}
                              >
                                <ArrowDownToLine size={16} />
                                下载
                              </button>
                            </div>
                          ))}
                        </div>
                      ))
                    ) : (
                      <div className="empty">
                        <p>Agent 交付后会在这里显示实际成果。</p>
                      </div>
                    )}
                  </section>
                  <div className="trust-note">
                    <CircleHelp size={20} />
                    <p>
                      系统按约定规则检查成果，检查通过不代表原始资料一定真实。请结合材料来源核对报告。
                    </p>
                  </div>
                </>
              )}
            </>
          )}
          </>}
          <footer>
            <span>AgentFlow Pay · DASC v1</span>
            <span>独立履约 · 依赖约束 · 可核验结算</span>
          </footer>
        </main>
      </div>
      <dialog
        ref={modalRef}
        onCancel={() => setCreateOpen(false)}
        onKeyDown={trapDialogTab}
        aria-labelledby="create-title"
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">NEW COLLABORATION</span>
            <h2 id="create-title">确认任务计划</h2>
          </div>
          <button
            aria-label="关闭创建窗口"
            className="icon-button"
            onClick={() => setCreateOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <label htmlFor="scenario">业务场景</label>
        <select id="scenario" value={scenario} disabled={!!busy}
          onChange={(e) => { setScenario(e.target.value); setPreview(null); setCreateError(""); }}>
          {(config.scenarios || []).map((s: AnyRecord) => <option key={s.id} value={s.id}>{s.title}</option>)}
        </select>
        <p>{config.scenarios?.find((s: AnyRecord) => s.id === scenario)?.description}</p>
        {(previewError || createError) && <p className="error" role="alert">{previewError || createError}</p>}
        <div className="input-list">
          <Database size={20} />
          <div>
            <strong>{preview ? `${preview.inputs.files.length} 份输入文件` : previewError ? "请先修正计划或输入资料" : "正在校验计划与输入资料"}</strong>
            <small>
              {preview?.inputs.request
                ? `${preview.inputs.request.destination} · ${preview.inputs.request.start_date} 出发 · ${preview.inputs.request.days} 天 · ${preview.inputs.request.travelers} 人`
                : preview ? `${preview.inputs.files.reduce((s: number, f: AnyRecord) => s + (f.rows || 0), 0).toLocaleString()} 行输入数据` : ""}
            </small>
          </div>
        </div>
        {preview?.inputs.request && <p className="hint">
          行程费用上限 ¥{(preview.inputs.request.budget_cents / 100).toFixed(2)}（住宿、门票、所列交通）；
          {preview.inputs.attractions} 个景点、{preview.inputs.hotels} 家酒店。
          资料：{preview.inputs.source}，截至 {preview.inputs.as_of}。提供行程方案，不进行预订。
        </p>}
        <div className="plan-steps">
          {preview?.plan.tasks.map((task: AnyRecord, index: number) => (
            <div key={task.key}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{task.title}</strong>
              <small>上限 {price(task.budget_cap)} 代币</small>
            </div>
          ))}
        </div>
        <div className="form-row">
          <div>
            <label htmlFor="budget">Agent 服务托管预算（代币）</label>
            <input
              id="budget"
              value={budget}
              disabled={!!busy}
              onChange={(e) => { setBudget(e.target.value); setPreview(null); }}
              inputMode="decimal"
            />
          </div>
          <div>
            <label htmlFor="sla">每轮结算时限（秒）</label>
            <input
              id="sla"
              type="number"
              min={10}
              max={120}
              value={sla}
              disabled={!!busy}
              onChange={(e) => { setSla(Number(e.target.value)); setPreview(null); }}
            />
          </div>
        </div>
        <p className="hint">
          节点预算合计 {preview ? amount(preview.plan.tasks.reduce((sum: bigint, t: AnyRecord) => sum + tokenUnits(t.budget_cap), 0n)) : "—"} 代币。
          此预算支付 Agent 规划服务；行程费用单独计算。成功节点按成交价结算，未使用部分终态后可退。
        </p>
        <div className="modal-actions">
          <button onClick={() => setCreateOpen(false)}>返回</button>
          <button
            className="primary"
            disabled={
              !!busy || !preview ||
              !/^\d+(\.\d{1,6})?$/.test(budget) ||
              Number(budget) < 90 ||
              sla < 10
            }
            onClick={() => void createJob()}
          >
            {busy === "create" ? (
              <Loader2 className="spin" size={17} />
            ) : (
              <LockKeyhole size={17} />
            )}
            托管预算并开始
          </button>
        </div>
      </dialog>
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
