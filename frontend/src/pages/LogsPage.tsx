import {
  ArrowLeft,
  ChevronRight,
  CircleAlert,
  ListRestart,
  RefreshCw,
  SquareTerminal,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { RuntimeLog, RuntimeLogLevel } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { formatTimestamp, levelMeta } from "../components/logFormat";
import { useToast } from "../store/toast";

const PAGE_SIZE = 100;

const EVENT_TYPE_SUGGESTIONS = [
  "translation.started",
  "translation.completed",
  "translation.stopped",
  "segment.claimed",
  "segment.tm_persisted",
  "segment.persisted",
  "segment.write_skipped",
  "segment.failed",
  "provider.requested",
  "provider.responded",
  "summary.requested",
  "summary.persisted",
  "summary.failed",
  "job.started",
  "job.completed",
  "job.failed",
  "job.stop_requested",
];

function LogEntryCard({ entry, onLocate }: { entry: RuntimeLog; onLocate: (id: number) => void }) {
  const meta = levelMeta[entry.level] ?? levelMeta.info;
  const Icon = meta.icon;
  const hasDetails = Object.keys(entry.details_json ?? {}).length > 0;
  return (
    <li className="rounded-xl border hairline bg-white p-3 dark:bg-ink-900">
      <div className="flex items-center gap-2">
        <span className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] ${meta.className}`}>
          <Icon className="size-3" />
          {meta.label}
        </span>
        <code className="truncate font-mono text-[10px] text-ink-400">{entry.event_type}</code>
        <time className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-ink-400">
          {formatTimestamp(entry.created_at)}
        </time>
      </div>
      <p className="mt-2 text-xs font-medium leading-5 text-ink-800 dark:text-ink-100">{entry.message}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10px] text-ink-400">
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
      {hasDetails ? (
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

export function LogsPage() {
  const params = useParams();
  const projectId = Number(params.projectId);
  const navigate = useNavigate();
  const { notify } = useToast();
  const queryClient = useQueryClient();

  const [level, setLevel] = useState<RuntimeLogLevel | undefined>();
  const [eventType, setEventType] = useState("");
  const [paused, setPaused] = useState(false);
  const [older, setOlder] = useState<RuntimeLog[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const filterKey = `${projectId}|${level ?? "all"}|${eventType.trim() || "all"}`;
  useEffect(() => {
    setOlder([]);
    setHasMore(false);
  }, [filterKey]);

  const projectQuery = useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => api.project(projectId),
    enabled: Number.isFinite(projectId),
  });
  const project = projectQuery.data;

  const logsQuery = useQuery({
    queryKey: queryKeys.runtimeLogs(projectId, level, eventType.trim() || undefined),
    queryFn: () =>
      api.runtimeLogs(projectId, {
        pageSize: PAGE_SIZE,
        level,
        eventType: eventType.trim() || undefined,
      }),
    enabled: Number.isFinite(projectId),
    refetchInterval: paused ? false : 2_000,
  });

  const loadMore = async () => {
    if (loadingOlder) return;
    const data = logsQuery.data;
    if (!data) return;
    const canFetch = hasMore || (older.length === 0 && data.has_more);
    if (!canFetch) return;
    const loaded = data.items.length + older.length;
    const nextPage = Math.floor(loaded / PAGE_SIZE) + 1;
    setLoadingOlder(true);
    try {
      const next = await api.runtimeLogs(projectId, {
        page: nextPage,
        pageSize: PAGE_SIZE,
        level,
        eventType: eventType.trim() || undefined,
      });
      setOlder((prev) => [...prev, ...next.items]);
      setHasMore(next.has_more);
    } catch {
      notify("加载更多日志失败。", "error");
    } finally {
      setLoadingOlder(false);
    }
  };

  const clearMutation = useMutation({
    mutationFn: () => api.deleteRuntimeLogs(projectId),
    onSuccess: () => {
      setConfirmClear(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.runtimeLogs(projectId) });
      notify("运行日志已清空。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const newest = logsQuery.data?.items ?? [];
  const rows = [...newest, ...older];
  const total = logsQuery.data?.total ?? 0;
  const canLoadMore = hasMore || (older.length === 0 && Boolean(logsQuery.data?.has_more));

  return (
    <div className="flex h-[calc(100vh-4rem)] min-h-[38rem] flex-col overflow-hidden">
      <header className="shrink-0 border-b hairline bg-paper/70 px-3 py-3 dark:bg-ink-950/55 sm:px-5">
        <div className="mx-auto flex max-w-[1920px] items-center gap-3">
          <button
            type="button"
            className="icon-btn"
            onClick={() => navigate(`/projects/${projectId}`)}
            aria-label="返回工作台"
            title="返回工作台"
          >
            <ArrowLeft className="size-5" />
          </button>
          <div className="min-w-0 flex-1">
            <h1 className="truncate font-serif text-lg font-semibold text-ink-950 dark:text-white sm:text-xl">
              {project?.title ?? "运行日志"}
            </h1>
            <p className="mt-0.5 truncate text-[11px] text-ink-400">
              运行日志 · {total > 0 ? `共 ${total} 条 · 保留最近 2000 条 / 1 天` : "暂无记录"}
            </p>
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setPaused((value) => !value)}
            disabled={logsQuery.isLoading}
            title={paused ? "恢复自动刷新" : "暂停自动刷新"}
            aria-label={paused ? "恢复自动刷新" : "暂停自动刷新"}
          >
            <ListRestart className={`size-4 ${paused ? "text-cinnabar-600" : ""}`} />
          </button>
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
          <button
            type="button"
            className="btn-danger hidden sm:inline-flex"
            onClick={() => setConfirmClear(true)}
            disabled={!total || clearMutation.isPending}
          >
            <Trash2 className="size-4" />
            {clearMutation.isPending ? "清空中" : "清空日志"}
          </button>
        </div>
      </header>

      <div className="shrink-0 border-b hairline bg-white/65 px-3 py-2 dark:bg-ink-900/60 sm:px-5">
        <div className="mx-auto flex max-w-[1920px] flex-wrap items-center gap-2">
          <select
            className="field min-h-9 w-36 py-1.5 text-xs"
            value={level ?? ""}
            onChange={(event) =>
              setLevel((event.target.value || undefined) as RuntimeLogLevel | undefined)
            }
            aria-label="日志级别"
          >
            <option value="">全部级别</option>
            <option value="error">错误</option>
            <option value="warning">警告</option>
            <option value="info">信息</option>
            <option value="debug">调试</option>
          </select>
          <div className="relative min-w-0 flex-1 sm:max-w-64">
            <input
              type="text"
              className="field min-h-9 w-full pr-8 text-xs"
              list="runtime-event-types"
              placeholder="事件类型过滤（如 provider.responded）"
              value={eventType}
              onChange={(event) => setEventType(event.target.value)}
              aria-label="事件类型过滤"
            />
            <datalist id="runtime-event-types">
              {EVENT_TYPE_SUGGESTIONS.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            {eventType ? (
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-400 hover:text-ink-600"
                onClick={() => setEventType("")}
                aria-label="清除事件类型过滤"
                title="清除过滤"
              >
                <X className="size-3.5" />
              </button>
            ) : null}
          </div>
          <span className="text-[11px] text-ink-400">
            {paused ? "已暂停自动刷新" : "每 2 秒自动刷新"}
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3 sm:px-5">
        {logsQuery.isLoading ? (
          <div className="flex h-full min-h-40 items-center justify-center">
            <RefreshCw className="size-5 animate-spin text-ink-400" />
          </div>
        ) : logsQuery.isError ? (
          <div className="py-14 text-center">
            <CircleAlert className="mx-auto size-5 text-cinnabar-600" />
            <p className="mt-2 text-xs leading-5 text-ink-500">{errorMessage(logsQuery.error)}</p>
            <button type="button" className="btn-ghost mt-2 text-xs" onClick={() => void logsQuery.refetch()}>
              重新加载
            </button>
          </div>
        ) : !rows.length ? (
          <EmptyState
            icon={SquareTerminal}
            title="暂无运行日志"
            description={
              eventType.trim() || level
                ? "当前过滤条件下没有匹配的记录，试试清除过滤条件。"
                : "启动翻译后，这里会记录摘要、模型请求、返回、写回与错误细节。"
            }
          />
        ) : (
          <>
            <p className="mb-2 text-[10px] text-ink-400">
              共 {total} 条 · 显示最新 {rows.length} 条
            </p>
            <ul className="mx-auto max-w-4xl space-y-2">
              {rows.map((entry) => (
                <LogEntryCard
                  key={entry.id}
                  entry={entry}
                  onLocate={(segmentId) => navigate(`/projects/${projectId}?segment=${segmentId}`)}
                />
              ))}
            </ul>
            {canLoadMore ? (
              <div className="mt-4 flex justify-center">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => void loadMore()}
                  disabled={loadingOlder}
                >
                  {loadingOlder ? <RefreshCw className="size-4 animate-spin" /> : <ChevronRight className="size-4" />}
                  加载更多
                </button>
              </div>
            ) : null}
          </>
        )}
      </div>

      <Modal
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title="清空运行日志？"
        description="将删除本项目全部运行日志，且无法恢复。"
        size="sm"
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setConfirmClear(false)}>
              取消
            </button>
            <button
              type="button"
              className="btn-danger"
              onClick={() => clearMutation.mutate()}
              disabled={clearMutation.isPending}
            >
              <Trash2 className="size-4" />
              {clearMutation.isPending ? "清空中" : "清空"}
            </button>
          </>
        }
      >
        <p className="text-sm text-ink-500 dark:text-ink-400">
          当前共有 {total} 条日志，清空后翻译过程中会重新写入新记录。
        </p>
      </Modal>
    </div>
  );
}
