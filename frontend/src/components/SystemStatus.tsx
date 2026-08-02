import { Boxes, CircleAlert, RefreshCw } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { ReleaseRecord } from "../api/types";
import { SettingSection } from "./SettingSection";
import { formatTimestamp } from "./logFormat";

const kindLabel: Record<string, string> = {
  boot: "启动",
  install: "安装",
  rollback: "回滚",
};

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`size-1.5 shrink-0 rounded-full ${ok ? "bg-emerald-500" : "bg-red-500"}`}
      title={ok ? "一致" : "不一致"}
    />
  );
}

export function SystemStatus() {
  const statusQuery = useQuery({
    queryKey: queryKeys.systemVersion,
    queryFn: api.systemVersion,
    staleTime: 60_000,
  });

  const { data, isError, error, isFetching, refetch, isLoading } = statusQuery;

  return (
    <SettingSection
      icon={Boxes}
      title="系统与更新"
      description="当前安装版本、数据库迁移状态与发布历史。安装与回滚由主机侧命令行工具执行。"
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs leading-5 text-ink-500">
          服务器端更新与降级由 <code className="rounded bg-ink-100 px-1 py-0.5 font-mono text-[11px] text-ink-700 dark:bg-ink-800 dark:text-ink-200">deploy/release.py</code> 管理，可在服务器上直接运行。
        </p>
        <button
          type="button"
          className="icon-btn size-7 shrink-0"
          onClick={() => void refetch()}
          disabled={isFetching}
          title="刷新"
          aria-label="刷新"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? "animate-spin" : ""}`} />
        </button>
      </div>

      {isLoading ? (
        <div className="flex min-h-32 items-center justify-center">
          <RefreshCw className="size-4 animate-spin text-ink-400" />
        </div>
      ) : isError ? (
        <div className="px-3 py-6 text-center">
          <CircleAlert className="mx-auto size-4 text-cinnabar-600" />
          <p className="mt-2 text-[11px] leading-5 text-ink-500">{errorMessage(error)}</p>
          <button type="button" className="btn-ghost mt-2 text-[11px]" onClick={() => void refetch()}>
            重新加载
          </button>
        </div>
      ) : data ? (
        <div className="mt-4 space-y-6">
          <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-400">版本</dt>
              <dd className="mt-1 flex flex-wrap items-center gap-2">
                <code className="rounded-md bg-ink-100 px-1.5 py-0.5 font-mono text-[13px] text-ink-800 dark:bg-ink-800 dark:text-ink-100">
                  {data.version}
                </code>
                {data.git_sha ? (
                  <code className="font-mono text-[11px] text-ink-400">#{data.git_sha.slice(0, 7)}</code>
                ) : null}
              </dd>
              {data.build_time ? (
                <dd className="mt-1 font-mono text-[11px] text-ink-400">
                  构建于 {formatTimestamp(data.build_time)}
                </dd>
              ) : null}
            </div>
            <div>
              <dt className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-400">
                <StatusDot ok={data.schema_ok} />
                数据库 Schema
              </dt>
              <dd className="mt-1 text-xs leading-5 text-ink-600 dark:text-ink-300">
                <span className={data.schema_ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}>
                  {data.schema_ok ? "一致" : "不一致"}
                </span>
              </dd>
              <dd className="mt-1 space-y-0.5 font-mono text-[11px] leading-4 text-ink-400">
                <div>代码库迁移头 {data.code_schema_head}</div>
                <div>数据库当前 {data.db_schema_revision ?? "未初始化"}</div>
              </dd>
            </div>
            <div>
              <dt className="text-[11px] font-medium uppercase tracking-wide text-ink-400">更新历史</dt>
              <dd className="mt-1 text-xs leading-5 text-ink-600 dark:text-ink-300">
                {data.releases.length} 条记录
              </dd>
            </div>
          </dl>

          <div>
            <h3 className="text-[11px] font-medium uppercase tracking-wide text-ink-400">发布历史</h3>
            {data.releases.length ? (
              <ul className="mt-2 divide-y hairline rounded-xl border hairline">
                {data.releases.map((record) => (
                  <ReleaseRow key={record.id} record={record} />
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-ink-500">暂无发布记录。</p>
            )}
          </div>

          <div>
            <h3 className="text-[11px] font-medium uppercase tracking-wide text-ink-400">常用命令</h3>
            <pre className="mt-2 overflow-x-auto rounded-xl border hairline bg-ink-950 px-4 py-3 font-mono text-[11px] leading-6 text-emerald-200 dark:bg-ink-900">
              {data.release_commands.map((command) => (
                <code key={command} className="block">
                  <span className="select-none text-ink-500">$ </span>
                  {command}
                </code>
              ))}
            </pre>
          </div>
        </div>
      ) : null}
    </SettingSection>
  );
}

function ReleaseRow({ record }: { record: ReleaseRecord }) {
  const failed = record.status !== "ok";
  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      <span
        className={`size-1.5 shrink-0 rounded-full ${failed ? "bg-red-500" : "bg-emerald-500"}`}
        title={record.status}
      />
      <code className="shrink-0 font-mono text-xs text-ink-700 dark:text-ink-200">{record.version}</code>
      <span className="shrink-0 rounded bg-ink-100 px-1.5 py-0.5 text-[11px] text-ink-500 dark:bg-ink-800 dark:text-ink-300">
        {kindLabel[record.kind] ?? record.kind}
      </span>
      <time className="ml-auto shrink-0 font-mono text-[11px] tabular-nums text-ink-400">
        {formatTimestamp(record.created_at)}
      </time>
    </li>
  );
}
