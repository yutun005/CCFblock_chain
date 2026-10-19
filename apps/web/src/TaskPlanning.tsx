import { useEffect, useState } from "react";
import { Background, Controls, ReactFlow } from "@xyflow/react";

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

export function PlanningGraph({ tasks }: { tasks: Row[] }) {
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
            {task.estimatedProvider || "等待前置工作完成后执行"}
          </div>
        ),
      },
      style: {
        width: 230,
        background: "#152638",
        color: "#eaf3ff",
        border: "1px solid #668ba9",
      },
    };
  });
  const edges = tasks.flatMap((task) =>
    task.dependencies.map((key: string) => ({
      id: `${key}-${task.key}`,
      source: key,
      target: task.key,
      style: { stroke: "#72b5ef", strokeWidth: 2 },
    })),
  );
  return (
    <div className="planning-graph" aria-label="子任务依赖图">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        minZoom={0.3}
      >
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function GraphEditor({
  value,
  scenario,
  capabilities,
  onChange,
  disabled = false,
}: {
  value: Row;
  scenario: string;
  capabilities: Row[];
  onChange: (value: Row) => void;
  disabled?: boolean;
}) {
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
  return (
    <>
      {!!value.tasks.length && <PlanningGraph tasks={value.tasks} />}
      <p className="hint">
        箭头表示前置依赖，同层任务可并行。可以修改步骤、标题和依赖；缺少必需前置时会提示修正，不会偷偷补收费任务。
      </p>
      <fieldset disabled={disabled}>
        <legend>最终需要哪些成果</legend>
        {outputs.map((key) => (
          <label className="check-row" key={key}>
            <input
              type="checkbox"
              checked={value.outputs.includes(key)}
              onChange={(e) =>
                change({
                  outputs: e.target.checked
                    ? [...value.outputs, key]
                    : value.outputs.filter((x: string) => x !== key),
                })
              }
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
        {value.tasks.map((task: Row) => (
          <section className="task-editor" key={task.key}>
            <h4>
              {skills.find((c) => c.skill === task.skill)?.title || task.skill}
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
              为什么需要这一步
              <textarea
                maxLength={500}
                value={task.reason}
                onChange={(e) =>
                  changeTask(task.key, { reason: e.target.value })
                }
              />
            </label>
            <fieldset>
              <legend>这一步要等待哪些任务</legend>
              {value.tasks
                .filter((t: Row) => t.key !== task.key)
                .map((other: Row) => (
                  <label className="check-row" key={other.key}>
                    <input
                      type="checkbox"
                      checked={task.dependencies.includes(other.key)}
                      onChange={(e) =>
                        changeTask(task.key, {
                          dependencies: e.target.checked
                            ? [...task.dependencies, other.key]
                            : task.dependencies.filter(
                                (x: string) => x !== other.key,
                              ),
                        })
                      }
                    />
                    {other.title}
                  </label>
                ))}
              {value.tasks.length === 1 && <p>没有其他子任务。</p>}
            </fieldset>
            <p className="hint">
              验收：{skills.find((c) => c.skill === task.skill)?.acceptance}
            </p>
            <button
              type="button"
              onClick={() =>
                change({
                  tasks: value.tasks
                    .filter((t: Row) => t.key !== task.key)
                    .map((t: Row) => ({
                      ...t,
                      dependencies: t.dependencies.filter(
                        (x: string) => x !== task.key,
                      ),
                    })),
                })
              }
            >
              移除此步骤
            </button>
          </section>
        ))}
        <label>
          添加子任务
          <select
            value=""
            onChange={(e) => {
              const c = skills.find((x) => x.skill === e.target.value);
              if (c)
                change({
                  tasks: [
                    ...value.tasks,
                    {
                      key: c.key,
                      skill: c.skill,
                      title: c.title,
                      reason: "为本次目标提供必要成果",
                      dependencies: [],
                    },
                  ],
                });
            }}
          >
            <option value="">选择当前支持的服务能力</option>
            {skills
              .filter((c) => !value.tasks.some((t: Row) => t.key === c.key))
              .map((c) => (
                <option value={c.skill} key={c.skill}>
                  {c.title}
                </option>
              ))}
          </select>
        </label>
      </fieldset>
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
  onDirty: () => void;
}) {
  const [draft, setDraft] = useState<Row>(plan.draft);
  const [dirty, setDirty] = useState(false);
  const [name, setName] = useState(recipe.title);
  const [saved, setSaved] = useState("");
  const change = (value: Row) => {
    setDraft(value);
    setDirty(true);
    onDirty();
  };
  return (
    <section className="planning-result">
      <h3>检查并确认子任务</h3>
      <p>本次目标：{recipe.requirement}</p>
      <p className="hint">
        来源：
        {plan.mode === "template_dag"
          ? "已有模板（未调用模型）"
          : "主 Agent 调用模型拆解"}
        。请核对这些工作是否符合您的预期。
      </p>
      <label>
        对目标的理解
        <textarea
          value={draft.summary}
          maxLength={1500}
          disabled={busy}
          onChange={(e) => change({ ...draft, summary: e.target.value })}
        />
      </label>
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
        disabled={busy || (!dirty && !!plan.reviewedAt)}
        onClick={() =>
          void run("review-plan", async () => {
            const result = await api(`${prefix}/planning/${plan.id}/review`, {
              recipe,
              draft,
            });
            onReviewed(result);
          })
        }
      >
        {plan.reviewedAt && !dirty
          ? "已确认任务图"
          : "检查通过，确认这些子任务"}
      </button>
      <p className="hint">
        确认任务图后才可比较执行方案；下一步确认托管时才开始付费执行。
      </p>
      <details>
        <summary>把这份任务拆解保存为模板</summary>
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
      <h3>{compact ? "先查找可复用的任务模板" : "任务模板库"}</h3>
      <p className="hint">
        {compact
          ? "按当前目标和场景查找。命中只代表关键词相近，采用前仍需核对子任务。"
          : "保留两套内置模板；自己的模板可修改、删除，也可复制成新模板。修改模板不会改变已经确认的任务。"}
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
              onClick={() => void run("use-template", () => onUse(item))}
            >
              采用并核对 / 微调
            </button>
          </article>
        ))}
      </div>
      {value && (
        <section className="workspace-panel">
          <h3>{editing ? "编辑模板" : value.name}</h3>
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
            disabled={!editing || busy}
          />
          {!compact && !editing && (
            <button
              type="button"
              disabled={busy}
              onClick={() => setEditing(true)}
            >
              {selected?.builtin ? "复制后编辑" : "编辑此模板"}
            </button>
          )}
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
