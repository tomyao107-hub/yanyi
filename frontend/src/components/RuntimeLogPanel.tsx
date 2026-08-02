import { ChevronRight, CircleAlert, RefreshCw, SquareTerminal } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { RuntimeLog } from "../api/types";
import { EmptyState } from "./EmptyState";
import { formatTimestamp, levelMeta } from "./logFormat";

function LogRow({ entry, onLocate }: { entry: RuntimeLog; onLocate: (id: number) => void }) {
  const meta = levelMeta[entry.level] ?? levelMeta.info;
  return (
    <li className="flex items-center gap-2 px-3 py-1.5 text-[11px] leading-none">
      <span className={`size-1.5 shrink-0 rounded-full ${meta.dotClass}`} title={meta.label} />
      <time className="shrink-0 font-mono tabular-nums text-ink-400">
        {formatTimestamp(entry.created_at)}
      </time>
      <span className="min-w-0 flex-1 truncate text-ink-700 dark:text-ink-200" title={entry.message}>
        {entry.message}
      </span>
      {entry.segment_id ? (
        <button
          type="button"
          className="shrink-0 font-mono text-cinnabar-700 underline-offset-2 hover:underline dark:text-cinnabar-400"
          onClick={() => onLocate(entry.segment_id as number)}
        >
          段 #{entry.segment_id}
        </button>
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
  const [paused, setPaused] = useState(false);
  const logsQuery = useQuery({
    queryKey: queryKeys.runtimeLogs(projectId),
    queryFn: () => api.runtimeLogs(projectId, { pageSize: 15 }),
    refetchInterval: paused ? false : 2_000,
  });

  return (
    <>
      <div className="flex items-center gap-2 border-b hairline px-3 py-2">
        <span className="text-[11px] font-medium text-ink-500">
          最近日志{logsQuery.data?.total ? ` · ${logsQuery.data.total} 条` : ""}
        </span>
        <button
          type="button"
          className="icon-btn size-6"
          onClick={() => setPaused((value) => !value)}
          title={paused ? "恢复自动刷新" : "暂停自动刷新"}
          aria-label={paused ? "恢复自动刷新" : "暂停自动刷新"}
        >
          <span
            className={`block h-2.5 w-2.5 rounded-sm border border-current ${
              paused ? "bg-ink-400 dark:bg-ink-200" : ""
            }`}
          />
        </button>
        <button
          type="button"
          className="icon-btn size-6"
          onClick={() => void logsQuery.refetch()}
          disabled={logsQuery.isFetching}
          title="刷新运行日志"
          aria-label="刷新运行日志"
        >
          <RefreshCw className={`size-3.5 ${logsQuery.isFetching ? "animate-spin" : ""}`} />
        </button>
        <Link
          to={`/projects/${projectId}/logs`}
          className="ml-auto inline-flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-cinnabar-700 hover:underline dark:text-cinnabar-400"
        >
          查看全部
          <ChevronRight className="size-3" />
        </Link>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {logsQuery.isLoading ? (
          <div className="flex min-h-32 items-center justify-center">
            <RefreshCw className="size-4 animate-spin text-ink-400" />
          </div>
        ) : logsQuery.isError ? (
          <div className="px-3 py-6 text-center">
            <CircleAlert className="mx-auto size-4 text-cinnabar-600" />
            <p className="mt-2 text-[11px] leading-5 text-ink-500">{errorMessage(logsQuery.error)}</p>
            <button type="button" className="btn-ghost mt-2 text-[11px]" onClick={() => void logsQuery.refetch()}>
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
          <ul>
            {logsQuery.data.items.map((entry) => (
              <LogRow key={entry.id} entry={entry} onLocate={onLocate} />
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
