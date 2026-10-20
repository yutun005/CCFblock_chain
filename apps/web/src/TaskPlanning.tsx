import { useEffect, useRef, useState } from "react";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
type Run = (name: string, action: () => Promise<void>) => Promise<void>;
const prefix = "/api/workspace";
const labels: Record<string, string> = {
  clean: "清洗后的数据",
  validate: "质量检查结果",
  statistics: "统计结果",
  charts: "图表",
  report: "完整报告",
  attractions: "景点推荐",
  hotels: "酒店推荐",
  route: "每日行程",
};

export function PlanningGraph({
  tasks,
  selectedTask,
  onSelectTask,
}: {
  tasks: Row[];
  selectedTask?: string;
  onSelectTask?: (key: string) => void;
}) {
  const fitGraph = useRef<(() => void) | null>(null);
  const layout = JSON.stringify(
    tasks.map((t) => [t.key, t.dependencies, t.title]),
  );
  useEffect(() => {
    const frame = requestAnimationFrame(() => fitGraph.current?.());
    return () => cancelAnimationFrame(frame);
  }, [layout]);
  const levels: Record<string, number> = {};
  for (let pass = 0; pass < tasks.length; pass++)
    for (const task of tasks)
      if (
        !(task.key in levels) &&
        task.dependencies.every((key: string) => key in levels)
      )
        levels[task.key] =
          Math.max(-1, ...task.dependencies.map((key: string) => levels[key])) +
          1;
  const columns: Record<number, number> = {};
  const nodes = tasks.map((task) => {
    const level = levels[task.key] || 0,
      column = columns[level] || 0;
    columns[level] = column + 1;
    return {
      id: task.key,
      position: { x: column * 260, y: level * 130 },
      data: {
        label: (
          <div>
            <b>{task.title}</b>
            <br />
            {task.estimatedProvider ||
              (task.dependencies.length ? "前置任务完成后执行" : "可直接开始")}
          </div>
        ),
      },
      style: {
        width: 230,
        background: "#152638",
        color: "#eaf3ff",
        border:
          task.key === selectedTask ? "2px solid #edc56c" : "1px solid #668ba9",
      },
      deletable: false,
    };
  });
  const edges = tasks.flatMap((task) =>
    task.dependencies.map((key: string) => ({
      id: `${key}-${task.key}`,
      source: key,
      target: task.key,
      ariaLabel: `${tasks.find((t) => t.key === key)?.title || key} → ${task.title}`,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#72b5ef" },
      style: { stroke: "#72b5ef", strokeWidth: 2 },
    })),
  );
  return (
    <>
      <p className="hint">箭头表示先后关系；没有连线依赖的任务可以同时进行。</p>
      <div className="planning-graph" aria-label="子任务依赖图">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          fitView
          onInit={(instance) => {
            fitGraph.current = () => {
              void instance.fitView({ padding: 0.15 });
            };
          }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={!!onSelectTask}
          onNodeClick={(_, node) => onSelectTask?.(node.id)}
          deleteKeyCode={null}
          minZoom={0.3}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </>
  );
}

function tasksForOutputs(outputs: string[], tasks: Row[], skills: Row[]) {
  const selected = new Map<string, Row>();
  const visiting = new Set<string>();
  const add = (key: string) => {
    if (selected.has(key)) return;
    if (visiting.has(key)) throw new Error("服务依赖存在循环，请检查服务定义");
    const capability = skills.find((c) => c.key === key);
    if (!capability) throw new Error("尚未接入该成果的执行服务，请调整目标");
    visiting.add(key);
    const dependencies = (capability.requires || []).map((type: string) => {
      const parent = skills.find((c) => c.outputType === type);
      if (!parent) throw new Error(`缺少 ${capability.title} 所需的前置服务`);
      add(parent.key);
      return parent.key;
    });
    visiting.delete(key);
    const original = tasks.find((t) => t.key === key);
    selected.set(key, {
      key,
      skill: capability.skill,
      title: original?.title || capability.title,
      reason: original?.reason || capability.acceptance || capability.title,
      dependencies,
    });
  };
  outputs.forEach((key) => add(key === "charts" ? "report" : key));
  return Array.from(selected.values());
}

function GraphEditor({
  value,
  scenario,
  capabilities,
  onChange,
  disabled = false,
  readOnly = false,
}: {
  value: Row;
  scenario: string;
  capabilities: Row[];
  onChange: (value: Row) => void;
  disabled?: boolean;
  readOnly?: boolean;
}) {
  const [selectedKey, setSelectedKey] = useState("");
  const [graphError, setGraphError] = useState("");
  const selected =
    value.tasks.find((t: Row) => t.key === selectedKey) || value.tasks[0];
  const skills = capabilities.filter((c) => c.scenario === scenario);
  const outputs =
    scenario === "travel_planning"
      ? ["attractions", "hotels", "route", "report"]
      : ["clean", "validate", "statistics", "charts", "report"];
  const change = (patch: Row) => onChange({ ...value, ...patch });
  const changeTask = (key: string, patch: Row) =>
    change({
      tasks: value.tasks.map((t: Row) =>
        t.key === key ? { ...t, ...patch } : t,
      ),
    });
  if (readOnly)
    return (
      <div className="template-preview" aria-label="模板内容预览">
        <p className="workspace-notice">
          模板预览（只读）。采用后可修改本次任务；模板原版会保留。
        </p>
        <p>
          <strong>最终交付：</strong>
          {value.outputs.map((key: string) => labels[key] || key).join("、") ||
            "尚未选择"}
        </p>
        {value.outputs.includes("report") && (
          <p>
            报告格式：
            {value.tier === "detailed"
              ? "文本报告、离线网页与来源对照表"
              : "文本报告与引用"}
          </p>
        )}
        {!!value.tasks.length && <PlanningGraph tasks={value.tasks} />}
        {value.tasks.map((task: Row) => (
          <p key={task.key}>
            <strong>{task.title}：</strong>
            {task.reason}
          </p>
        ))}
      </div>
    );
  return (
    <>
      <fieldset className="deliverable-picker" disabled={disabled}>
        <legend>最终需要哪些成果</legend>
        <p className="hint">
          选择成果后，任务和连线会自动调整；仍被其他成果使用的步骤会保留。
        </p>
        {outputs.map((key) => (
          <label className="check-row" key={key}>
            <input
              type="checkbox"
              checked={value.outputs.includes(key)}
              onChange={(e) => {
                const next = e.target.checked
                  ? [...value.outputs, key]
                  : value.outputs.filter((x: string) => x !== key);
                try {
                  const tasks = tasksForOutputs(next, value.tasks, skills);
                  setGraphError("");
                  change({
                    outputs: next,
                    tasks,
                    tier: next.includes("report") ? value.tier : "standard",
                  });
                } catch (err) {
                  setGraphError(String(err));
                }
              }}
            />
            {labels[key]}
          </label>
        ))}
        {scenario === "cross_org_analysis" && (
          <label>
            统计分组
            <select
              value={value.groupBy || "organization_quarter"}
              onChange={(e) => change({ groupBy: e.target.value })}
            >
              <option value="organization_quarter">组织与季度</option>
              <option value="organization">组织</option>
              <option value="category">商品类别</option>
              <option value="month">月份</option>
            </select>
          </label>
        )}
        {value.outputs.includes("report") && (
          <label>
            报告格式
            <select
              value={value.tier || "standard"}
              onChange={(e) => change({ tier: e.target.value })}
            >
              <option value="standard">文本报告与引用</option>
              <option value="detailed">增加离线网页与来源对照表</option>
            </select>
          </label>
        )}
      </fieldset>
      {graphError && <p role="alert">{graphError}</p>}
      <div className="graph-workbench">
        <section className="graph-canvas">
          <h3>
            任务安排{" "}
            <span className="count-label">{value.tasks.length} 项工作</span>
          </h3>
          {!!value.tasks.length && (
            <PlanningGraph
              tasks={value.tasks}
              selectedTask={selected?.key}
              onSelectTask={setSelectedKey}
            />
          )}
          {!value.tasks.length && (
            <p className="empty-materials">
              请选择需要交付的成果，任务图会自动生成。
            </p>
          )}
        </section>
        <fieldset className="node-inspector" disabled={disabled}>
          <legend>任务详情</legend>
          <div className="task-selectors">
            {value.tasks.map((task: Row, index: number) => (
              <button
                type="button"
                aria-label={`编辑任务：${task.title}`}
                aria-pressed={selected?.key === task.key}
                key={task.key}
                onClick={() => setSelectedKey(task.key)}
              >
                <span>{index + 1}</span>
                {task.title}
              </button>
            ))}
          </div>
          {(selected ? [selected] : []).map((task: Row) => (
            <section className="task-editor" key={task.key}>
              <h4>
                {skills.find((c) => c.skill === task.skill)?.title ||
                  task.skill}
              </h4>
              <label>
                子任务名称
                <input
                  maxLength={120}
                  value={task.title}
                  onChange={(e) =>
                    changeTask(task.key, { title: e.target.value })
                  }
                />
              </label>
              <label>
                工作内容
                <textarea
                  maxLength={500}
                  value={task.reason}
                  onChange={(e) =>
                    changeTask(task.key, { reason: e.target.value })
                  }
                />
              </label>
              <p className="hint">
                验收：{skills.find((c) => c.skill === task.skill)?.acceptance}
              </p>
            </section>
          ))}
        </fieldset>
      </div>
    </>
  );
}

export function PlanReview({
  plan,
  recipe,
  capabilities,
  api,
  run,
  busy,
  onReviewed,
  onDirty,
}: {
  plan: Row;
  recipe: Row;
  capabilities: Row[];
  api: Api;
  run: Run;
  busy: boolean;
  onReviewed: (value: Row) => void;
  onDirty: (draft: Row) => void;
}) {
  const [draft, setDraft] = useState<Row>(plan.draft);
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState(recipe.title);
  const [saved, setSaved] = useState("");
  const change = (value: Row) => {
    setDraft(value);
    setDirty(true);
    setSaved("");
    onDirty(value);
  };
  return (
    <section className="planning-result">
      <div className="request-brief">
        <span className="subtle-badge">
          {plan.mode === "template_dag" ? "来自模板" : "Agent 生成"}
        </span>
        <p>{recipe.requirement}</p>
      </div>
      <details>
        <summary>查看与修改方案说明</summary>
        <label>
          对目标的理解
          <textarea
            value={draft.summary}
            maxLength={1500}
            disabled={busy}
            onChange={(e) => change({ ...draft, summary: e.target.value })}
          />
        </label>
      </details>
      {(draft.limitations || []).map((limit: string, i: number) => (
        <p className="hint" key={i}>
          {limit}
        </p>
      ))}
      <GraphEditor
        value={draft}
        scenario={recipe.scenario}
        capabilities={capabilities}
        disabled={busy}
        onChange={change}
      />
      <button
        type="button"
        className="primary"
        disabled={
          busy ||
          !draft.outputs.length ||
          !draft.tasks.length ||
          (!dirty && !!plan.reviewedAt)
        }
        onClick={() =>
          void run("review-plan", async () => {
            const result = await api(`${prefix}/planning/${plan.id}/review`, {
              recipe,
              draft,
            });
            onReviewed(result);
            setDirty(false);
          })
        }
      >
        {plan.reviewedAt && !dirty ? "已确认任务图" : "确认任务方案"}
      </button>
      <p className="hint">确认方案不会付款；下一页确认预算托管后才开始执行。</p>
      <details>
        <summary>保存到模板库，下次复用</summary>
        <label>
          模板名称
          <input
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={
            busy || dirty || !plan.reviewedAt || !name.trim() || !!saved
          }
          onClick={() =>
            void run("save-template", async () => {
              const result = await api(prefix + "/templates", {
                template: {
                  name,
                  description: draft.summary,
                  scenario: recipe.scenario,
                  tags: [],
                  outputs: draft.outputs,
                  tasks: draft.tasks,
                  tier: draft.tier || recipe.tier,
                  groupBy: draft.groupBy || recipe.groupBy,
                },
              });
              setSaved(result.name);
            })
          }
        >
          保存为新模板
        </button>
        <p className="hint">
          只保存子任务、依赖、交付与分组方式。材料、出行日期、预算、执行者和付款不写入模板。
        </p>
        {saved && (
          <p role="status">已保存“{saved}”，可以在模板库中查找和管理。</p>
        )}
      </details>
    </section>
  );
}

export function TemplateLibrary({
  api,
  run,
  busy,
  capabilities,
  onUse,
  compact = false,
  query = "",
  scenario = "",
}: {
  api: Api;
  run: Run;
  busy: boolean;
  capabilities: Row[];
  onUse: (template: Row) => Promise<void>;
  compact?: boolean;
  query?: string;
  scenario?: string;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("");
  const [items, setItems] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<Row | null>(null);
  const [message, setMessage] = useState("");
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const term = compact ? query : search,
    kind = compact ? scenario : filter;
  useEffect(() => {
    let canceled = false;
    const timer = setTimeout(
      () => {
        void api(
          `${prefix}/templates?q=${encodeURIComponent(term)}&scenario=${encodeURIComponent(kind)}`,
        )
          .then((rows) => {
            if (!canceled) {
              setItems(rows);
              setLoadError("");
            }
          })
          .catch((err) => {
            if (!canceled) {
              setItems([]);
              setLoadError(String(err));
            }
          });
      },
      compact ? 300 : 0,
    );
    return () => {
      canceled = true;
      clearTimeout(timer);
    };
  }, [api, term, kind, compact, reload]);
  const open = (item: Row) => {
    setSelected(item);
    setValue(item);
    setEditing(false);
    setDeleting(false);
    setMessage("");
  };
  const adopt = async (item: Row) => {
    await onUse(item);
    setValue(null);
    setSelected(null);
  };
  const editable = (item: Row) =>
    Object.fromEntries(
      [
        "name",
        "description",
        "scenario",
        "tags",
        "outputs",
        "tasks",
        "tier",
        "groupBy",
      ].map((k) => [k, k === "tags" ? item.tags.filter(Boolean) : item[k]]),
    );
  return (
    <section className="template-library">
      <h2>{compact ? "可用模板" : "模板库"}</h2>
      <p className="hint">
        {compact
          ? "选一个作为起点，再按本次目标编辑。"
          : "保存常用的任务方案。选用后可修改本次任务，原模板保持不变。"}
      </p>
      {!compact && (
        <>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setReload((n) => n + 1);
              setSelected(null);
              setValue(null);
            }}
          >
            刷新模板库
          </button>
          <label>
            搜索名称、目标或关键词
            <input value={search} onChange={(e) => setSearch(e.target.value)} />
          </label>
          <label>
            场景
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">所有场景</option>
              <option value="cross_org_analysis">销售数据分析</option>
              <option value="travel_planning">旅行规划</option>
            </select>
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setSelected(null);
              setEditing(true);
              setMessage("");
              setValue({
                name: "",
                description: "",
                tags: [],
                scenario: filter || "cross_org_analysis",
                outputs: [],
                tasks: [],
                tier: "standard",
                groupBy: "organization_quarter",
              });
            }}
          >
            新建模板
          </button>
        </>
      )}
      {loadError && <p role="alert">{loadError}</p>}
      {!items.length && !loadError && (
        <p>
          {compact
            ? "尚未找到匹配模板，可以让主 Agent 拆解新目标。"
            : "没有匹配的模板。"}
        </p>
      )}
      <div className="template-cards">
        {items.map((item) => (
          <article className="task-editor" key={item.id}>
            <h4>
              {item.name}
              {item.builtin ? " · 内置" : ""}
            </h4>
            <p>{item.description}</p>
            <p>
              {item.tasks.length} 个步骤 · 交付：
              {item.outputs.map((x: string) => labels[x]).join("、")}
            </p>
            <button type="button" disabled={busy} onClick={() => open(item)}>
              查看{compact ? "子任务" : " / 管理"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run("use-template", () => adopt(item))}
            >
              用此模板创建任务
            </button>
          </article>
        ))}
      </div>
      {value && (
        <section className="workspace-panel">
          <h3>{editing ? "编辑模板" : value.name}</h3>
          {!editing && selected && (
            <div className="workspace-actions">
              <button
                type="button"
                disabled={busy}
                onClick={() => void run("use-template", () => adopt(selected))}
              >
                用此模板创建任务
              </button>
              {!compact && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setEditing(true)}
                >
                  {selected.builtin ? "复制后编辑" : "编辑此模板"}
                </button>
              )}
            </div>
          )}
          {editing && (
            <>
              <label>
                名称
                <input
                  value={value.name}
                  maxLength={100}
                  disabled={busy}
                  onChange={(e) => setValue({ ...value, name: e.target.value })}
                />
              </label>
              <label>
                适用目标
                <textarea
                  value={value.description}
                  maxLength={1500}
                  disabled={busy}
                  onChange={(e) =>
                    setValue({ ...value, description: e.target.value })
                  }
                />
              </label>
              <label>
                检索关键词（逗号分隔）
                <input
                  value={value.tags.join(",")}
                  disabled={busy}
                  onChange={(e) =>
                    setValue({
                      ...value,
                      tags: e.target.value
                        .split(/[,，]/)
                        .map((t: string) => t.trim()),
                    })
                  }
                />
              </label>
              {!selected && (
                <label>
                  场景
                  <select
                    value={value.scenario}
                    disabled={busy}
                    onChange={(e) =>
                      setValue({
                        ...value,
                        scenario: e.target.value,
                        tasks: [],
                        outputs: [],
                      })
                    }
                  >
                    <option value="cross_org_analysis">销售数据分析</option>
                    <option value="travel_planning">旅行规划</option>
                  </select>
                </label>
              )}
            </>
          )}
          <GraphEditor
            value={value}
            scenario={value.scenario}
            capabilities={capabilities}
            onChange={setValue}
            readOnly={!editing}
            disabled={busy}
          />
          {editing && (
            <>
              {selected && !selected.builtin && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void run("update-template", async () => {
                      const item = await api(
                        `${prefix}/templates/${selected.id}/update`,
                        {
                          template: editable(value),
                          revision: selected.revision,
                        },
                      );
                      open(item);
                      setMessage("修改已保存；已确认的旧任务保持原状。");
                      setReload((n) => n + 1);
                    })
                  }
                >
                  保存对当前模板的修改
                </button>
              )}
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run("create-template", async () => {
                    const item = await api(prefix + "/templates", {
                      template: editable(value),
                    });
                    open(item);
                    setMessage("已保存为新模板。");
                    setReload((n) => n + 1);
                  })
                }
              >
                {selected ? "另存为新模板" : "保存模板"}
              </button>
            </>
          )}
          {!compact && selected && !selected.builtin && !editing && (
            <>
              {!deleting ? (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setDeleting(true)}
                >
                  删除此模板
                </button>
              ) : (
                <>
                  <p>确定删除“{selected.name}”？已有任务和成果会保留。</p>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void run("delete-template", async () => {
                        await api(`${prefix}/templates/${selected.id}/delete`, {
                          revision: selected.revision,
                        });
                        setSelected(null);
                        setValue(null);
                        setMessage("模板已删除。");
                        setReload((n) => n + 1);
                      })
                    }
                  >
                    确定删除
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setDeleting(false)}
                  >
                    取消
                  </button>
                </>
              )}
            </>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setValue(null);
              setSelected(null);
            }}
          >
            关闭
          </button>
        </section>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
