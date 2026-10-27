import { useState } from "react";
import { ArrowRight, FileCheck2 } from "lucide-react";
import { ReviewInbox, TaskOutput } from "./Deliverables";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
export const taskTitle = (job: Row) =>
  job.title ||
  job.goal ||
  job.plan?.goal ||
  (job.scenario === "travel_planning" ||
  job.plan?.scenario === "travel_planning"
    ? "旅行规划任务"
    : "数据分析任务");
const statuses: Record<string, string> = {
  ACTIVE: "执行中",
  FUNDING: "正在托管",
  SUCCEEDED: "已完成",
  PARTIAL_FAILED: "部分未完成",
  INTERRUPTED: "已中断",
  AWAITING_REVIEW: "待验收",
  REWORKING: "修改中",
};

export function TaskList({
  jobs,
  onOpen,
  onResults,
  empty = "还没有任务。",
}: {
  jobs: Row[];
  onOpen: (id: string) => void;
  onResults?: (id: string) => void;
  empty?: string;
}) {
  const [query, setQuery] = useState("");
  const filtered = jobs.filter((j) =>
    taskTitle(j).toLowerCase().includes(query.trim().toLowerCase()),
  );
  return (
    <div className="task-collection">
      {!!jobs.length && (
        <label className="task-search">
          查找任务
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="按任务名称查找"
          />
        </label>
      )}
      {!jobs.length ? (
        <p className="collection-empty">{empty}</p>
      ) : !filtered.length ? (
        <p>没有找到匹配的任务。</p>
      ) : (
        <ul className="task-list">
          {filtered.map((j) => (
            <li key={j.jobId}>
              <button
                className="task-list-main"
                onClick={() => onOpen(j.jobId)}
              >
                <FileCheck2 size={21} />
                <span>
                  <strong>{taskTitle(j)}</strong>
                  <small>
                    {j.createdAt
                      ? new Date(j.createdAt * 1000).toLocaleString("zh-CN", {
                          year: "numeric",
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "已保存"}
                    {j.plan?.tasks?.length
                      ? ` · ${j.plan.tasks.length} 项工作`
                      : ""}
                  </small>
                </span>
                <span
                  className={`tag ${j.status === "SUCCEEDED" ? "text-good" : ""}`}
                >
                  {statuses[j.reviewStatus] || statuses[j.status] || "待处理"}
                </span>
                <ArrowRight size={17} />
              </button>
              {onResults && (
                <button
                  className="task-list-results"
                  onClick={() => onResults(j.jobId)}
                >
                  查看成果
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TaskResults({
  api,
  job,
  artifacts,
  finalTaskKeys,
  onChange,
  onSettlement,
}: {
  api: Api;
  job: Row;
  artifacts: Row[];
  finalTaskKeys?: string[];
  onChange: () => Promise<unknown>;
  onSettlement: (id: string) => void;
}) {
  const [reviews, setReviews] = useState<Row[] | null>(null);
  const parents = new Set(job.tasks.flatMap((t: Row) => t.dependencies));
  const isFinal = (t: Row) =>
    finalTaskKeys?.length ? finalTaskKeys.includes(t.key) : !parents.has(t.key);
  const group = (tasks: Row[], label: string) =>
    tasks.length > 0 && (
      <section className="panel result-task-group" aria-label={label}>
        <h2>{label}</h2>
        {tasks.map((t) => {
          const delivery =
            artifacts.find((a) => a.resultHash === t.onchain.resultHash) ||
            artifacts.filter((a) => a.manifest.taskId === t.taskId).at(-1);
          const files = delivery?.manifest.artifacts || [];
          return (
            <section className="content-section" key={t.key}>
              <h3 className="section-title">
                <strong>{t.title}</strong>
                <span>
                  {files.length ? `${files.length} 个文件` : "查看交付进度"}
                </span>
              </h3>
              <TaskOutput
                api={api}
                jobId={job.jobId}
                taskId={t.taskId}
                revision={t.onchain.resultHash || String(t.onchain.attempt)}
              />
            </section>
          );
        })}
      </section>
    );
  return (
    <div className="task-results">
      <ReviewInbox
        api={api}
        jobId={job.jobId}
        onChange={onChange}
        onSettlement={onSettlement}
        onLoaded={setReviews}
      />
      {reviews &&
        group(
          job.tasks.filter(
            (t: Row) =>
              isFinal(t) &&
              !reviews.some(
                (r: Row) => r.taskId === t.taskId && r.drafts?.length,
              ),
          ),
          "最终交付文件",
        )}
      {group(
        job.tasks.filter((t: Row) => !isFinal(t)),
        "中间成果",
      )}
    </div>
  );
}
