import { AlertTriangle, Bug, CircleAlert, Info, RefreshCw, SquareTerminal } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { RuntimeLog, RuntimeLogLevel } from "../api/types";
import { EmptyState } from "./EmptyState";

const levelMeta: Record<
  RuntimeLogLevel,
  { label: string; className: string; icon: typeof Info }
> = {
  debug: {
    label: "调试",
    className: "border-ink-200 bg-ink-50 text-ink-500 dark:border-ink-700 dark:bg-ink-800",
    icon: Bug,
  },
  info: {
    label: "信息",
    className: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300",
    icon: Info,
  },
  warning: {
    label: "警告",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300",
    icon: AlertTriangle,
  },
  error: {
    label: "错误",
    className: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    icon: CircleAlert,
  },
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function LogEntry({ entry, onLocate }: { entry: RuntimeLog; onLocate: (id: number) => void }) {
  const meta = levelMeta[entry.level] ?? levelMeta.info;
  const Icon = meta.icon;
  const details = Object.keys(entry.details_json ?? {}).length > 0;
  return (
    <li className="rounded-xl border hairline bg-white p-3 dark:bg-ink-900">
      <div className="flex items-start gap-2">
        <span className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] ${meta.className}`}>
          <Icon className="size-3" />
          {meta.label}
        </span>
        <time className="ml-auto font-mono text-[10px] tabular-nums text-ink-400">
          {formatTimestamp(entry.created_at)}
        </time>
      </div>
      <p className="mt-2 text-xs font-medium leading-5 text-ink-800 dark:text-ink-100">
        {entry.message}
      </p>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] text-ink-400">
        <span>{entry.event_type}</span>
        {entry.job_id ? <span>任务 #{entry.job_id}</span> : null}
        {entry.chapter_id ? <span>章节 #{entry.chapter_id}</span> : null}
        {entry.segment_id ? (
          <button
            type="button"
            className="text-cinnabar-700 underline-offset-2 hover:underline dark:text-cinnabar-400"
            onClick={() => onLocate(entry.segment_id as number)}
          >
            段落 #{entry.segment_id}
          </button>
        ) : null}
      </div>
      {details ? (
        <details className="mt-2 rounded-lg bg-ink-50 px-2 py-1.5 dark:bg-ink-800/70">
          <summary className="cursor-pointer text-[10px] text-ink-500">查看运行细节</summary>
          <pre className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-4 text-ink-500 dark:text-ink-300">
            {JSON.stringify(entry.details_json, null, 2)}
          </pre>
        </details>
      ) : null}
    </li>
  );
}

export function RuntimeLogPanel({
  projectId,
  onLocate,
}: {
  projectId: number;
  onLocate: (segmentId: number) => void;
}) {
  const [level, setLevel] = useState<RuntimeLogLevel | undefined>();
  const logsQuery = useQuery({
    queryKey: queryKeys.runtimeLogs(projectId, level),
    queryFn: () => api.runtimeLogs(projectId, { pageSize: 200, level }),
    refetchInterval: 2_000,
  });

  return (
    <>
      <div className="flex items-center gap-2 border-b hairline p-3">
        <select
          className="field min-h-9 flex-1 py-1.5 text-xs"
          value={level ?? ""}
          onChange={(event) =>
            setLevel((event.target.value || undefined) as RuntimeLogLevel | undefined)
          }
          aria-label="日志级别"
        >
          <option value="">全部级别</option>
          <option value="info">信息</option>
          <option value="warning">警告</option>
          <option value="error">错误</option>
          <option value="debug">调试</option>
        </select>
        <button
          type="button"
          className="icon-btn"
          onClick={() => void logsQuery.refetch()}
          disabled={logsQuery.isFetching}
          title="刷新运行日志"
          aria-label="刷新运行日志"
        >
          <RefreshCw className={`size-4 ${logsQuery.isFetching ? "animate-spin" : ""}`} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {logsQuery.isLoading ? (
          <div className="flex min-h-40 items-center justify-center">
            <RefreshCw className="size-5 animate-spin text-ink-400" />
          </div>
        ) : logsQuery.isError ? (
          <div className="py-10 text-center">
            <CircleAlert className="mx-auto size-5 text-cinnabar-600" />
            <p className="mt-2 text-xs leading-5 text-ink-500">{errorMessage(logsQuery.error)}</p>
            <button type="button" className="btn-ghost mt-2 text-xs" onClick={() => void logsQuery.refetch()}>
              重新加载
            </button>
          </div>
        ) : !logsQuery.data?.items.length ? (
          <EmptyState
            compact
            icon={SquareTerminal}
            title="暂无运行日志"
            description="启动翻译后，这里会记录摘要、模型请求、返回、写回与错误细节。"
          />
        ) : (
          <>
            <p className="mb-2 text-[10px] text-ink-400">
              共 {logsQuery.data.total} 条，显示最新 {logsQuery.data.items.length} 条 · 每 2 秒刷新
            </p>
            <ul className="space-y-2">
              {logsQuery.data.items.map((entry) => (
                <LogEntry key={entry.id} entry={entry} onLocate={onLocate} />
              ))}
            </ul>
          </>
        )}
      </div>
    </>
  );
}
