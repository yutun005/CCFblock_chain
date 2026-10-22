import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowDownToLine,
  Check,
  GitBranch,
  LayoutTemplate,
  MessageSquare,
  Plus,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { PlanningGraph } from "./TaskPlanning";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
const prefix = "/api/workspace";
const phases: Row = {
  clarifying: "完善需求",
  outline: "讨论方案",
  dag: "编辑任务图",
  confirmed: "已确认",
};
const lines = (value: string) =>
  value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

function GraphEditor({
  graph,
  brief,
  outline,
  disabled,
  onSave,
  onDirty,
}: {
  graph: Row;
  brief: Row;
  outline: Row[];
  disabled: boolean;
  onSave: (value: Row) => Promise<boolean>;
  onDirty: (dirty: boolean) => void;
}) {
  const [value, setValue] = useState<Row>(graph);
  const [selected, setSelected] = useState(graph.tasks[0]?.key || "");
  const [dirty, setDirty] = useState(false);
  const task =
    value.tasks.find((node: Row) => node.key === selected) || value.tasks[0];
  const change = (next: Row) => {
    setValue(next);
    setDirty(true);
    onDirty(true);
  };
  const patchTask = (patch: Row) =>
    change({
      ...value,
      tasks: value.tasks.map((node: Row) =>
        node.key === task.key ? { ...node, ...patch } : node,
      ),
    });
  const remove = () => {
    change({
      ...value,
      tasks: value.tasks
        .filter((node: Row) => node.key !== task.key)
        .map((node: Row) => ({
          ...node,
          dependencies: node.dependencies.filter(
            (key: string) => key !== task.key,
          ),
        })),
      deliverables: value.deliverables.filter(
        (item: Row) => item.taskKey !== task.key,
      ),
    });
    setSelected("");
  };
  const add = () => {
    const key = "task_" + crypto.randomUUID().replaceAll("-", "").slice(0, 12);
    change({
      ...value,
      tasks: [
        ...value.tasks,
        {
          key,
          title: "新任务",
          description: "请说明工作内容和边界",
          stageId: outline[0].id,
          dependencies: [],
          inputs: [],
          outputs: ["新任务成果"],
          acceptance: ["请填写可核对的验收条件"],
          requirementIds: [brief.requirements[0].id],
        },
      ],
    });
    setSelected(key);
  };
  return (
    <section className="design-graph-editor" aria-label="开放任务图编辑器">
      <div className="design-section-heading">
        <h3>
          任务 DAG <span>{value.tasks.length} 项工作</span>
        </h3>
        <button
          type="button"
          disabled={disabled || value.tasks.length >= 64}
          onClick={add}
        >
          <Plus size={15} />
          添加任务
        </button>
      </div>
      <PlanningGraph
        tasks={value.tasks}
        selectedTask={task?.key}
        onSelectTask={setSelected}
        planningOnly
      />
      <div className="design-node-tabs">
        {value.tasks.map((node: Row) => (
          <button
            type="button"
            key={node.key}
            aria-pressed={node.key === task?.key}
            onClick={() => setSelected(node.key)}
          >
            {node.title}
          </button>
        ))}
      </div>
      {task && (
        <fieldset className="design-node-form" disabled={disabled}>
          <legend>编辑任务：{task.title}</legend>
          <label>
            任务名称
            <input
              maxLength={120}
              value={task.title}
              onChange={(e) => patchTask({ title: e.target.value })}
            />
          </label>
          <label>
            所属阶段
            <select
              value={task.stageId}
              onChange={(e) => patchTask({ stageId: e.target.value })}
            >
              {outline.map((stage: Row) => (
                <option key={stage.id} value={stage.id}>
                  {stage.title}
                </option>
              ))}
            </select>
          </label>
          <label className="design-wide">
            工作内容与边界
            <textarea
              maxLength={2000}
              value={task.description}
              onChange={(e) => patchTask({ description: e.target.value })}
            />
          </label>
          <label>
            需要哪些输入（每行一项）
            <textarea
              value={task.inputs.join("\n")}
              onChange={(e) =>
                patchTask({ inputs: e.target.value.split("\n") })
              }
            />
          </label>
          <label>
            产出什么（每行一项）
            <textarea
              value={task.outputs.join("\n")}
              onChange={(e) => {
                const outputs = e.target.value.split("\n");
                change({
                  ...value,
                  tasks: value.tasks.map((node: Row) =>
                    node.key === task.key ? { ...node, outputs } : node,
                  ),
                  deliverables: value.deliverables.map((item: Row) =>
                    item.taskKey === task.key &&
                    task.outputs.includes(item.name)
                      ? {
                          ...item,
                          name:
                            outputs[task.outputs.indexOf(item.name)] ||
                            item.name,
                        }
                      : item,
                  ),
                });
              }}
            />
          </label>
          <label className="design-wide">
            怎样验收（每行一项）
            <textarea
              value={task.acceptance.join("\n")}
              onChange={(e) =>
                patchTask({ acceptance: e.target.value.split("\n") })
              }
            />
          </label>
          <fieldset className="design-wide">
            <legend>前置任务</legend>
            {value.tasks
              .filter((node: Row) => node.key !== task.key)
              .map((node: Row) => (
                <label className="check-row" key={node.key}>
                  <input
                    type="checkbox"
                    checked={task.dependencies.includes(node.key)}
                    onChange={(e) =>
                      patchTask({
                        dependencies: e.target.checked
                          ? [...task.dependencies, node.key]
                          : task.dependencies.filter(
                              (key: string) => key !== node.key,
                            ),
                      })
                    }
                  />
                  {node.title}
                </label>
              ))}
            {value.tasks.length === 1 && (
              <p className="hint">当前没有其他任务。</p>
            )}
          </fieldset>
          <fieldset className="design-wide">
            <legend>对应客户要求</legend>
            {brief.requirements.map((req: Row) => (
              <label className="check-row" key={req.id}>
                <input
                  type="checkbox"
                  checked={task.requirementIds.includes(req.id)}
                  onChange={(e) =>
                    patchTask({
                      requirementIds: e.target.checked
                        ? [...task.requirementIds, req.id]
                        : task.requirementIds.filter(
                            (key: string) => key !== req.id,
                          ),
                    })
                  }
                />
                {req.text}
              </label>
            ))}
          </fieldset>
          <fieldset className="design-wide">
            <legend>作为最终交付</legend>
            {task.outputs.map((output: string, index: number) => (
              <label className="check-row" key={index}>
                <input
                  type="checkbox"
                  checked={value.deliverables.some(
                    (d: Row) => d.taskKey === task.key && d.name === output,
                  )}
                  onChange={(e) =>
                    change({
                      ...value,
                      deliverables: e.target.checked
                        ? [
                            ...value.deliverables,
                            { name: output, taskKey: task.key },
                          ]
                        : value.deliverables.filter(
                            (d: Row) =>
                              !(d.taskKey === task.key && d.name === output),
                          ),
                    })
                  }
                />
                {output || "请先填写输出名称"}
              </label>
            ))}
          </fieldset>
          <button
            type="button"
            className="design-delete"
            disabled={value.tasks.length <= 1}
            onClick={remove}
          >
            移除这项任务
          </button>
        </fieldset>
      )}
      <fieldset disabled={disabled}>
        <legend>最终交付清单</legend>
        <p className="hint">
          修改输出名称后，可移除旧项，再在对应任务中勾选新的输出。
        </p>
        {value.deliverables.map((item: Row, index: number) => (
          <div className="design-toolbar" key={`${item.taskKey}-${index}`}>
            <span>
              {item.name} ·{" "}
              {value.tasks.find((node: Row) => node.key === item.taskKey)
                ?.title || item.taskKey}
            </span>
            <button
              type="button"
              onClick={() =>
                change({
                  ...value,
                  deliverables: value.deliverables.filter(
                    (_: Row, i: number) => i !== index,
                  ),
                })
              }
            >
              移除交付项
            </button>
          </div>
        ))}
      </fieldset>
      <div className="design-edit-footer">
        <p className="hint">
          {dirty
            ? "修改尚未保存。保存时检查依赖、循环和需求覆盖。"
            : "点击节点可改工作内容、输入输出、验收条件和依赖，也可以直接向 AI 提修改意见。"}
        </p>
        <button
          type="button"
          disabled={disabled || !dirty}
          onClick={async () => {
            const cleaned = {
              ...value,
              tasks: value.tasks.map((node: Row) => ({
                ...node,
                inputs: lines(node.inputs.join("\n")),
                outputs: lines(node.outputs.join("\n")),
                acceptance: lines(node.acceptance.join("\n")),
              })),
            };
            if (await onSave(cleaned)) {
              setDirty(false);
              onDirty(false);
            }
          }}
        >
          保存任务图修改
        </button>
      </div>
    </section>
  );
}

function TemplateLibrary({
  templates,
  busy,
  onApply,
}: {
  templates: Row[];
  busy: boolean;
  onApply: (template: Row) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const items = templates.filter((t) =>
    `${t.name} ${t.description} ${t.category}`
      .toLowerCase()
      .includes(query.trim().toLowerCase()),
  );
  return (
    <section className="design-template-library" aria-label="开放规划模板库">
      <header className="design-header">
        <div>
          <span className="eyebrow">模板库</span>
          <h1>从一份任务图开始</h1>
          <p>
            选择模板后直接复制
            DAG，再按本次需求修改。也可以把自己的方案保存到这里。
          </p>
        </div>
      </header>
      <label className="design-template-search">
        搜索模板
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="搜索目标、名称或类别"
        />
      </label>
      <div className="design-template-grid">
        {items.map((template) => (
          <article key={template.id}>
            <span className="subtle-badge">{template.category}</span>
            <h2>{template.name}</h2>
            <p>{template.description}</p>
            <small>
              {template.graph.tasks.length} 个任务 ·{" "}
              {template.graph.deliverables.length} 项交付
            </small>
            <div className="workspace-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => setSelected(template)}
              >
                预览任务图
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => void onApply(template)}
              >
                按此模板生成 DAG
              </button>
            </div>
          </article>
        ))}
      </div>
      {!items.length && (
        <p>没有匹配的模板。可以先与 AI 讨论方案，再保存自己的模板。</p>
      )}
      {selected && (
        <section className="design-template-preview">
          <div className="design-section-heading">
            <h2>{selected.name}</h2>
            <button
              type="button"
              aria-label="关闭模板预览"
              onClick={() => setSelected(null)}
            >
              <X size={17} />
            </button>
          </div>
          <PlanningGraph tasks={selected.graph.tasks} planningOnly />
          {selected.graph.tasks.map((task: Row) => (
            <p key={task.key}>
              <strong>{task.title}：</strong>
              {task.description}
            </p>
          ))}
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void onApply(selected)}
          >
            按此模板生成 DAG
          </button>
        </section>
      )}
    </section>
  );
}

export default function DesignWorkspace({
  api,
  mode,
  onOpenChat,
  onOpenTemplates,
}: {
  api: Api;
  mode: "chat" | "templates";
  onOpenChat: () => void;
  onOpenTemplates: () => void;
}) {
  const [session, setSession] = useState<Row | null>(null);
  const [sessions, setSessions] = useState<Row[]>([]);
  const [templates, setTemplates] = useState<Row[]>([]);
  const [planner, setPlanner] = useState<Row | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dirty, setDirty] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [version, setVersion] = useState<Row | null>(null);
  const tail = useRef<HTMLDivElement>(null);
  const graphAnchor = useRef<HTMLDivElement>(null);
  const openedGraph = useRef("");
  const refresh = useCallback(async () => {
    const results = await Promise.allSettled([
      api(prefix + "/designs"),
      api(prefix + "/design-templates"),
      api(prefix + "/planner"),
    ]);
    if (results[0].status === "fulfilled") setSessions(results[0].value);
    if (results[1].status === "fulfilled") setTemplates(results[1].value);
    if (results[2].status === "fulfilled") setPlanner(results[2].value);
    const failure = results.find((item) => item.status === "rejected");
    if (failure?.status === "rejected") throw failure.reason;
  }, [api]);
  useEffect(() => {
    void refresh().catch((err) => setError(String(err)));
  }, [refresh]);
  useEffect(() => {
    const log = tail.current?.parentElement;
    log?.scrollTo({ top: log.scrollHeight, behavior: "smooth" });
  }, [session?.revision]);
  useEffect(() => {
    const key = session?.graph ? session.id : "";
    if (key && key !== openedGraph.current) {
      const frame = requestAnimationFrame(() =>
        graphAnchor.current
          ?.querySelector(".planning-graph")
          ?.scrollIntoView({ block: "center", behavior: "instant" }),
      );
      openedGraph.current = key;
      return () => cancelAnimationFrame(frame);
    }
    openedGraph.current = key;
  }, [session?.id, session?.graph]);
  const run = async (action: string, fn: () => Promise<void>) => {
    setBusy(action);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };
  const adopt = async (result: Row) => {
    setSession(result);
    setDirty(false);
    setVersion(null);
    await refresh();
  };
  const send = async (message = text) => {
    if (!message.trim()) return;
    if (dirty) throw new Error("请先保存任务图修改，再继续与 AI 讨论。");
    const result = session
      ? await api(`${prefix}/designs/${session.id}/messages`, {
          message,
          expectedRevision: session.revision,
        })
      : await api(prefix + "/designs", { message });
    setText("");
    await adopt(result);
  };
  const mutate = async (action: string, extra: Row = {}) => {
    if (!session) return;
    const result = await api(`${prefix}/designs/${session.id}/${action}`, {
      expectedRevision: session.revision,
      ...extra,
    });
    await adopt(result);
  };
  const download = (value: Row) => {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            schemaVersion: "agentflow-design-v1",
            planningOnly: true,
            ...value,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = "任务方案-DAG.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const applyTemplate = async (template: Row) =>
    run("apply", async () => {
      const result = await api(
        `${prefix}/design-templates/${template.id}/apply`,
        { revision: template.revision },
      );
      await adopt(result);
      onOpenChat();
      setNotice(
        "已按模板生成独立任务图，原模板会保留。可以直接编辑，或向 AI 描述调整要求。",
      );
    });
  return (
    <section
      className="open-design-workspace"
      aria-label="项目与成果规划工作区"
    >
      {error && (
        <div role="alert" className="error">
          {error}
          <button
            type="button"
            disabled={!!busy}
            onClick={() => void run("refresh", refresh)}
          >
            重新连接
          </button>
        </div>
      )}
      {notice && (
        <p role="status" className="workspace-notice">
          {notice}
        </p>
      )}
      {dirty && mode === "templates" && (
        <p className="workspace-notice">
          当前有未保存的任务图修改，请返回“项目与成果”保存后再使用其他模板。
        </p>
      )}
      <div hidden={mode !== "templates"}>
        <TemplateLibrary
          templates={templates}
          busy={!!busy || dirty}
          onApply={applyTemplate}
        />
      </div>
      <div hidden={mode !== "chat"}>
        <header className="design-header">
          <div>
            <span className="eyebrow">项目与成果 · 开放式方案规划</span>
            <h1>与 AI 一起，把想法变成任务图</h1>
            <p>先给一份粗略 DAG，不必一次说清所有要求；边看、边聊、边修改。</p>
          </div>
          <div className="workspace-actions">
            <button
              type="button"
              disabled={!!busy || dirty}
              onClick={() => {
                setSession(null);
                setVersion(null);
                setText("");
                setError("");
                setNotice("");
              }}
            >
              <Plus size={17} />
              新方案
            </button>
            <button type="button" disabled={!!busy} onClick={onOpenTemplates}>
              <LayoutTemplate size={17} />
              浏览模板库
            </button>
          </div>
        </header>
        <div className="design-layout">
          <aside className="design-history">
            <h2>我的方案</h2>
            {!sessions.length && (
              <p className="hint">对话与任务图会自动保存，之后可以继续修改。</p>
            )}
            {sessions.map((item) => (
              <button
                type="button"
                key={item.id}
                aria-pressed={session?.id === item.id}
                disabled={!!busy || dirty}
                onClick={() =>
                  void run("open", async () => {
                    const result = await api(`${prefix}/designs/${item.id}`);
                    setSession(result);
                    setVersion(null);
                    setText("");
                    setDirty(false);
                  })
                }
              >
                <strong>{item.title}</strong>
                <small>
                  {phases[item.phase]} · 第 {item.revision} 稿
                </small>
              </button>
            ))}
          </aside>
          <section className="design-conversation" aria-label="与 AI 讨论方案">
            <div className="design-conversation-heading">
              <MessageSquare size={18} />
              <strong>方案讨论</strong>
              {session && <span>{phases[session.phase]}</span>}
            </div>
            <div
              className="design-messages"
              role="log"
              aria-label="方案对话记录"
            >
              {!session && (
                <div className="design-welcome">
                  <Sparkles size={30} />
                  <h2>你希望完成什么？</h2>
                  <p>
                    可以是产品设计、活动策划、学习路线、调研写作，或你自己的工作。先说目标，不必先选业务类型。
                  </p>
                  <div className="design-starters">
                    {[
                      "为校园读书节设计一套活动方案",
                      "我想做一个宠物用品小程序",
                      "帮我规划学习机器学习的路线",
                    ].map((example) => (
                      <button
                        type="button"
                        key={example}
                        disabled={!!busy}
                        onClick={() => setText(example)}
                      >
                        {example}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {session?.messages.map((message: Row) => (
                <article
                  key={message.id}
                  className={`design-message ${message.role}`}
                >
                  <span>{message.role === "user" ? "你" : "规划助手"}</span>
                  <div>{message.content}</div>
                  {!!message.questions?.length && (
                    <ul>
                      {message.questions.map(
                        (question: string, index: number) => (
                          <li key={index}>{question}</li>
                        ),
                      )}
                    </ul>
                  )}
                </article>
              ))}
              {busy && (
                <p role="status" className="design-thinking">
                  {busy === "expand"
                    ? "正在把已确认主干展开为任务图…"
                    : busy === "confirm"
                      ? "正在核对需求并保存确认版本…"
                      : busy === "review"
                        ? "正在整理可选的查漏建议…"
                        : busy === "send"
                          ? "正在理解要求、完善方案或生成任务图…"
                          : "正在保存…"}
                </p>
              )}
              <div ref={tail} />
            </div>
            {!planner?.configured && planner && (
              <div className="design-model-notice">
                <strong>模型尚未配置</strong>
                <p>
                  填写项目根目录 planner.local.json 的接口地址、模型与 API
                  Key，便可开始对话。模板可以直接使用。
                </p>
                <button
                  type="button"
                  disabled={!!busy}
                  onClick={() => void run("refresh", refresh)}
                >
                  重新检查模型配置
                </button>
              </div>
            )}
            <form
              className="design-composer"
              onSubmit={(event) => {
                event.preventDefault();
                void run("send", () => send());
              }}
            >
              <label className="sr-only" htmlFor="design-message">
                告诉 AI 你的目标或修改意见
              </label>
              <textarea
                id="design-message"
                value={text}
                maxLength={12000}
                disabled={!!busy}
                onChange={(e) => setText(e.target.value)}
                placeholder={
                  session
                    ? "补充信息，或说说哪里不满意、希望怎样调整…"
                    : "描述你想完成的目标、期望成果，以及已有的条件…"
                }
              />
              <div>
                <small>
                  当前先形成方案与任务图，节点功能接入后再安排执行。
                </small>
                <button
                  type="submit"
                  className="primary"
                  disabled={
                    !!busy || !text.trim() || !planner?.configured || dirty
                  }
                >
                  <Send size={16} />
                  发送给 AI
                </button>
              </div>
            </form>
          </section>
          <section className="design-canvas" aria-label="当前方案与任务图">
            {!session ? (
              <div className="design-canvas-empty">
                <GitBranch size={42} />
                <h2>先看草稿，再逐步完善</h2>
                <p>1. 说一个大致方向，就可以先生成 DAG</p>
                <p>2. 暂不清楚的条件留作待补，继续对话修改</p>
                <p>3. 满意后确认当前版本，之后仍能继续调整</p>
                <button type="button" onClick={onOpenTemplates}>
                  也可以从模板开始
                </button>
              </div>
            ) : (
              <>
                <div className="design-section-heading">
                  <h2>{session.title}</h2>
                  <span className="subtle-badge">{phases[session.phase]}</span>
                </div>
                <details className="design-context" open={!session.graph}>
                  <summary>需求与方案说明</summary>
                  <section className="design-brief">
                    <h3>我们理解的需求</h3>
                    <p>{session.brief.objective}</p>
                    <ul>
                      {session.brief.requirements.map((req: Row) => (
                        <li key={req.id}>{req.text}</li>
                      ))}
                    </ul>
                    {!!session.brief.knownInputs?.length && (
                      <p>
                        <strong>已有条件：</strong>
                        {session.brief.knownInputs.join("；")}
                      </p>
                    )}
                    {!!session.brief.assumptions?.length && (
                      <p className="hint">
                        <strong>待你核对的假设：</strong>
                        {session.brief.assumptions.join("；")}
                      </p>
                    )}
                  </section>
                  {session.phase === "clarifying" && (
                    <div className="design-pending">
                      <strong>这些信息可以后续再补</strong>
                      <p>可以先看草稿，再决定要补充哪些条件。</p>
                      <button
                        type="button"
                        disabled={!!busy}
                        onClick={() =>
                          void run("expand", () => mutate("expand"))
                        }
                      >
                        先生成草稿 DAG
                      </button>
                    </div>
                  )}
                  {!!session.outline.length && (
                    <section className="design-outline">
                      <h3>方案主干</h3>
                      {session.outline.map((stage: Row, index: number) => (
                        <article key={stage.id}>
                          <span>{index + 1}</span>
                          <div>
                            <h4>{stage.title}</h4>
                            <p>{stage.description}</p>
                            <small>交付：{stage.deliverables.join("、")}</small>
                          </div>
                        </article>
                      ))}
                      {session.phase === "outline" && (
                        <>
                          <p className="hint">
                            可以先生成草稿，不要求现在把所有信息补齐。
                          </p>
                          <button
                            type="button"
                            className="primary"
                            disabled={!!busy}
                            onClick={() =>
                              void run("expand", () => mutate("expand"))
                            }
                          >
                            先生成草稿 DAG
                          </button>
                        </>
                      )}
                    </section>
                  )}
                </details>
                {session.graph && (
                  <>
                    <div ref={graphAnchor}>
                      <GraphEditor
                        key={`${session.id}:${session.revision}`}
                        graph={session.graph}
                        brief={session.brief}
                        outline={session.outline}
                        disabled={!!busy}
                        onDirty={setDirty}
                        onSave={async (graph) => {
                          let saved = false;
                          await run("edit", async () => {
                            await mutate("edit", { graph });
                            saved = true;
                          });
                          return saved;
                        }}
                      />
                    </div>
                    {!!(
                      session.warnings?.length || session.questions?.length
                    ) && (
                      <details className="design-suggestions">
                        <summary>后续可完善的事项（不影响保存和确认）</summary>
                        {[
                          ...(session.warnings || []),
                          ...(session.questions || []),
                        ].map((item: string, index: number) => (
                          <p key={index}>{item}</p>
                        ))}
                      </details>
                    )}
                    {!!session.coverage?.length && (
                      <details className="design-coverage">
                        <summary>查看需求与任务的对应关系</summary>
                        {session.coverage.map((row: Row) => (
                          <p key={row.id}>
                            <strong>{row.text}</strong>
                            <br />
                            {row.taskKeys
                              .map(
                                (key: string) =>
                                  session.graph.tasks.find(
                                    (t: Row) => t.key === key,
                                  )?.title || key,
                              )
                              .join("、")}
                          </p>
                        ))}
                      </details>
                    )}
                    <section className="design-finalize">
                      <h3>最终交付</h3>
                      <p className="hint">
                        确认会保存你认可的当前版本，尚未明确的条件可以继续保留。AI
                        查漏仅供参考。
                      </p>
                      <ul>
                        {session.graph.deliverables.map(
                          (item: Row, index: number) => (
                            <li key={index}>{item.name}</li>
                          ),
                        )}
                      </ul>
                      <div className="workspace-actions">
                        <button
                          type="button"
                          className="primary"
                          disabled={
                            !!busy || dirty || session.phase === "confirmed"
                          }
                          onClick={() =>
                            void run("confirm", () => mutate("confirm"))
                          }
                        >
                          <Check size={16} />
                          {session.phase === "confirmed"
                            ? "此版已确认"
                            : "确认这版任务图"}
                        </button>
                        <button
                          type="button"
                          disabled={!!busy || dirty || !planner?.configured}
                          onClick={() =>
                            void run("review", () => mutate("review"))
                          }
                        >
                          AI 查漏（可选）
                        </button>
                        <button
                          type="button"
                          disabled={dirty}
                          onClick={() =>
                            download({
                              title: session.title,
                              brief: session.brief,
                              outline: session.outline,
                              graph: session.graph,
                            })
                          }
                        >
                          <ArrowDownToLine size={16} />
                          导出 DAG
                        </button>
                      </div>
                      {session.review?.source === "model_suggestions" && (
                        <div className="design-suggestions" role="status">
                          <strong>AI 查漏建议</strong>
                          {session.review.issues.length ? (
                            session.review.issues.map(
                              (issue: string, index: number) => (
                                <p key={index}>{issue}</p>
                              ),
                            )
                          ) : (
                            <p>未发现明显遗漏，你仍可按自己的需要调整。</p>
                          )}
                        </div>
                      )}
                      {!!session.graph.limitations.length && (
                        <p className="hint">
                          {session.graph.limitations.join("；")}
                        </p>
                      )}
                      <details>
                        <summary>保存为可复用模板</summary>
                        <label>
                          模板名称
                          <input
                            maxLength={100}
                            value={templateName}
                            onChange={(e) => setTemplateName(e.target.value)}
                          />
                        </label>
                        <button
                          type="button"
                          disabled={!!busy || dirty || !templateName.trim()}
                          onClick={() =>
                            void run("save-template", async () => {
                              await api(
                                `${prefix}/designs/${session.id}/template`,
                                {
                                  expectedRevision: session.revision,
                                  name: templateName.trim(),
                                },
                              );
                              await refresh();
                              setNotice("模板已保存，可以在左侧模板库中找到。");
                              setTemplateName("");
                            })
                          }
                        >
                          保存到模板库
                        </button>
                      </details>
                    </section>
                  </>
                )}
                {!!session.versions.length && (
                  <section className="design-versions">
                    <h3>已确认版本</h3>
                    <div className="workspace-actions">
                      {session.versions.map((item: Row) => (
                        <button
                          type="button"
                          key={item.id}
                          disabled={!!busy}
                          onClick={() =>
                            void run("version", async () =>
                              setVersion(
                                await api(
                                  `${prefix}/designs/${session.id}/versions/${item.id}`,
                                ),
                              ),
                            )
                          }
                        >
                          查看第 {item.number} 版
                        </button>
                      ))}
                    </div>
                    {version && (
                      <div>
                        <p>
                          第 {version.number} 版 · {version.graph.title}
                        </p>
                        <button type="button" onClick={() => download(version)}>
                          导出此确认版本
                        </button>
                        <PlanningGraph
                          tasks={version.graph.tasks}
                          planningOnly
                        />
                      </div>
                    )}
                  </section>
                )}
              </>
            )}
          </section>
        </div>
      </div>
    </section>
  );
}
