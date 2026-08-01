import {
  AlertCircle,
  BarChart3,
  Check,
  ChevronRight,
  CircleAlert,
  Download,
  Database,
  ListChecks,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  SquareTerminal,
  SpellCheck2,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { GlossaryTerm, QaIssue } from "../api/types";
import { useToast } from "../store/toast";
import { EmptyState } from "./EmptyState";
import { Modal } from "./Modal";
import { RuntimeLogPanel } from "./RuntimeLogPanel";

type SidebarTab = "glossary" | "qa" | "memory" | "logs";
type TermInput = Omit<GlossaryTerm, "id" | "project_id">;

const emptyTerm: TermInput = {
  source_term: "",
  target_term: "",
  note: "",
  case_sensitive: false,
  enabled: true,
};

function downloadCsvTemplate() {
  const content =
    "\uFEFFsource_term,target_term,note,case_sensitive,enabled\n" +
    '"Artificial Intelligence","人工智能","保持全书统一",false,true\n';
  const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "glossary-template.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

function TermEditor({
  open,
  term,
  pending,
  onClose,
  onSave,
}: {
  open: boolean;
  term: GlossaryTerm | null;
  pending: boolean;
  onClose: () => void;
  onSave: (input: TermInput) => void;
}) {
  const [draft, setDraft] = useState<TermInput>(emptyTerm);

  useEffect(() => {
    if (!open) return;
    setDraft(
      term
        ? {
            source_term: term.source_term,
            target_term: term.target_term,
            note: term.note ?? "",
            case_sensitive: term.case_sensitive,
            enabled: term.enabled,
          }
        : emptyTerm,
    );
  }, [open, term]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft.source_term.trim() && draft.target_term.trim()) {
      onSave({
        ...draft,
        source_term: draft.source_term.trim(),
        target_term: draft.target_term.trim(),
        note: draft.note?.trim() ?? "",
      });
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={term ? "编辑术语" : "添加术语"}
      description="术语会注入每段翻译的上下文，帮助专名与概念前后一致。"
      size="sm"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={pending}>取消</button>
          <button type="submit" form="term-editor-form" className="btn-primary" disabled={pending}>
            <Check className="size-4" />
            {pending ? "保存中" : "保存术语"}
          </button>
        </>
      }
    >
      <TermEditorForm draft={draft} setDraft={setDraft} onSubmit={submit} />
    </Modal>
  );
}

function TermEditorForm({
  draft,
  setDraft,
  onSubmit,
}: {
  draft: TermInput;
  setDraft: React.Dispatch<React.SetStateAction<TermInput>>;
  onSubmit: (event: FormEvent) => void;
}) {
  return (
    <form id="term-editor-form" onSubmit={onSubmit} className="space-y-4">
      <div>
        <label className="field-label" htmlFor="term-source">原文术语</label>
        <input
          id="term-source"
          className="field"
          value={draft.source_term}
          onChange={(event) => setDraft((current) => ({ ...current, source_term: event.target.value }))}
          autoFocus
          required
        />
      </div>
      <div>
        <label className="field-label" htmlFor="term-target">固定译法</label>
        <input
          id="term-target"
          className="field"
          value={draft.target_term}
          onChange={(event) => setDraft((current) => ({ ...current, target_term: event.target.value }))}
          required
        />
      </div>
      <div>
        <label className="field-label" htmlFor="term-note">备注（可选）</label>
        <textarea
          id="term-note"
          className="field min-h-20 resize-y"
          value={draft.note ?? ""}
          onChange={(event) => setDraft((current) => ({ ...current, note: event.target.value }))}
          placeholder="人物、地点或使用语境"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex cursor-pointer items-center gap-2 rounded-lg border hairline p-3 text-xs text-ink-600 dark:text-ink-300">
          <input
            type="checkbox"
            checked={draft.case_sensitive}
            onChange={(event) => setDraft((current) => ({ ...current, case_sensitive: event.target.checked }))}
            className="size-4 accent-cinnabar-700"
          />
          区分大小写
        </label>
        <label className="flex cursor-pointer items-center gap-2 rounded-lg border hairline p-3 text-xs text-ink-600 dark:text-ink-300">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))}
            className="size-4 accent-cinnabar-700"
          />
          启用此术语
        </label>
      </div>
    </form>
  );
}

function severityStyle(issue: QaIssue): string {
  return issue.severity === "error"
    ? "border-cinnabar-200 bg-cinnabar-50 text-cinnabar-700 dark:border-cinnabar-900 dark:bg-cinnabar-950/30 dark:text-cinnabar-300"
    : issue.severity === "info"
      ? "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300"
      : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300";
}

function TranslationMemoryPanel({ projectId }: { projectId: number }) {
  const statsQuery = useQuery({
    queryKey: queryKeys.tmStats(projectId),
    queryFn: () => api.tmStats(projectId),
    retry: false,
  });
  const stats = statsQuery.data;
  const entries = Number(
    stats?.entries ??
      stats?.entry_count ??
      stats?.total_entries ??
      stats?.language_pair_entries ??
      0,
  );
  const hits = Number(stats?.hits ?? stats?.hit_count ?? stats?.total_hits ?? stats?.reused_segments ?? 0);
  const projectSegments = Number(stats?.project_segments ?? 0);
  const projectMatches = Number(stats?.project_tm_matches ?? 0);
  const coverageRate =
    projectSegments > 0 ? (projectMatches / projectSegments) * 100 : 0;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-ink-900 dark:text-white">翻译记忆</p>
          <p className="mt-1 text-xs leading-5 text-ink-500">
            相同原文会直接复用既有译文，减少模型调用并保持一致。
          </p>
        </div>
        <button
          type="button"
          className="icon-btn"
          onClick={() => void statsQuery.refetch()}
          disabled={statsQuery.isFetching}
          aria-label="刷新翻译记忆统计"
          title="刷新统计"
        >
          <RefreshCw className={`size-4 ${statsQuery.isFetching ? "animate-spin" : ""}`} />
        </button>
      </div>

      {statsQuery.isError ? (
        <div className="mt-5 rounded-xl border border-dashed border-ink-200 p-5 text-center dark:border-ink-700">
          <Database className="mx-auto size-5 text-ink-400" />
          <p className="mt-2 text-xs font-medium text-ink-600 dark:text-ink-300">统计暂不可用</p>
          <p className="mt-1 text-[10px] leading-4 text-ink-400">翻译记忆仍会在后端正常工作。</p>
        </div>
      ) : (
        <>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <div className="rounded-xl border hairline bg-white p-4 dark:bg-ink-900">
              <Database className="size-4 text-cinnabar-600" />
              <span className="mt-3 block font-serif text-2xl font-semibold tabular-nums text-ink-950 dark:text-white">
                {statsQuery.isLoading ? "—" : entries.toLocaleString("zh-CN")}
              </span>
              <span className="mt-1 block text-[10px] uppercase tracking-wider text-ink-400">记忆条目</span>
            </div>
            <div className="rounded-xl border hairline bg-white p-4 dark:bg-ink-900">
              <BarChart3 className="size-4 text-emerald-600" />
              <span className="mt-3 block font-serif text-2xl font-semibold tabular-nums text-ink-950 dark:text-white">
                {statsQuery.isLoading ? "—" : hits.toLocaleString("zh-CN")}
              </span>
              <span className="mt-1 block text-[10px] uppercase tracking-wider text-ink-400">累计复用次数</span>
            </div>
          </div>
          <div className="mt-3 rounded-xl border hairline bg-white p-4 dark:bg-ink-900">
            <div className="flex items-center justify-between text-xs">
              <span className="text-ink-500">当前书目记忆覆盖率</span>
              <span className="font-mono font-medium tabular-nums text-ink-800 dark:text-ink-100">
                {statsQuery.isLoading ? "—" : `${Math.max(0, Math.min(100, coverageRate)).toFixed(1)}%`}
              </span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-ink-100 dark:bg-ink-800">
              <div
                className="h-full rounded-full bg-emerald-600 transition-[width]"
                style={{ width: `${Math.max(0, Math.min(100, coverageRate))}%` }}
              />
            </div>
            {Number(stats?.reusable_remaining_segments ?? 0) > 0 ? (
              <p className="mt-3 text-[10px] text-ink-400">
                尚有 {Number(stats?.reusable_remaining_segments).toLocaleString("zh-CN")} 个待译段可直接复用
              </p>
            ) : null}
            {Number(stats?.saved_tokens ?? 0) > 0 ? (
              <p className="mt-3 text-[10px] text-ink-400">
                估计已节省 {Number(stats?.saved_tokens).toLocaleString("zh-CN")} tokens
              </p>
            ) : null}
          </div>
        </>
      )}

      <div className="mt-5 rounded-xl bg-ink-50 p-4 dark:bg-ink-800/55">
        <p className="text-xs font-medium text-ink-700 dark:text-ink-200">如何提升命中</p>
        <p className="mt-1.5 text-[11px] leading-5 text-ink-500">
          保持原文版本稳定；重复的版权页、章节标题与固定表达会自动跨书复用。
        </p>
      </div>
    </div>
  );
}

export function WorkbenchSidebar({
  projectId,
  onLocate,
}: {
  projectId: number;
  onLocate: (segmentId: number) => void;
}) {
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<SidebarTab>("glossary");
  const [search, setSearch] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingTerm, setEditingTerm] = useState<GlossaryTerm | null>(null);
  const [deleteTerm, setDeleteTerm] = useState<GlossaryTerm | null>(null);

  const glossaryQuery = useQuery({
    queryKey: queryKeys.glossary(projectId),
    queryFn: () => api.glossary(projectId),
  });
  const qaQuery = useQuery({
    queryKey: queryKeys.qa(projectId),
    queryFn: () => api.qa(projectId),
    enabled: tab === "qa",
  });

  const saveMutation = useMutation({
    mutationFn: (input: TermInput) =>
      editingTerm
        ? api.updateGlossaryTerm(editingTerm.id, input)
        : api.createGlossaryTerm(projectId, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.glossary(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.qa(projectId) });
      setEditorOpen(false);
      setEditingTerm(null);
      notify(editingTerm ? "术语已更新。" : "术语已添加。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api.updateGlossaryTerm(id, { enabled }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.glossary(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.qa(projectId) });
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteGlossaryTerm(id),
    onSuccess: () => {
      setDeleteTerm(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.glossary(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.qa(projectId) });
      notify("术语已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const importMutation = useMutation({
    mutationFn: (file: File) => api.importGlossaryCsv(projectId, file, true),
    onSuccess: ({ created, updated }) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.glossary(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.qa(projectId) });
      notify(`CSV 导入完成：新增 ${created} 条，更新 ${updated} 条。`, "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const importCsv = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".csv")) {
      notify("请选择 CSV 文件。", "error");
      return;
    }
    importMutation.mutate(file);
  };

  const filteredTerms = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    return (glossaryQuery.data ?? []).filter((item) =>
      `${item.source_term} ${item.target_term} ${item.note ?? ""}`.toLocaleLowerCase().includes(term),
    );
  }, [glossaryQuery.data, search]);

  const qaCount = qaQuery.data?.length;

  return (
    <div className="flex h-full min-h-0 flex-col bg-white/70 dark:bg-ink-900/70">
      <div className="grid grid-cols-4 border-b hairline p-2">
        <button
          type="button"
          className={`flex min-h-9 items-center justify-center gap-2 rounded-lg text-xs font-medium transition ${
            tab === "glossary"
              ? "bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-950"
              : "text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          }`}
          onClick={() => setTab("glossary")}
        >
          <SpellCheck2 className="size-4" />
          术语表
          {glossaryQuery.data?.length ? (
            <span className="rounded-full bg-white/15 px-1.5 text-[10px]">{glossaryQuery.data.length}</span>
          ) : null}
        </button>
        <button
          type="button"
          className={`flex min-h-9 items-center justify-center gap-2 rounded-lg text-xs font-medium transition ${
            tab === "qa"
              ? "bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-950"
              : "text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          }`}
          onClick={() => setTab("qa")}
        >
          <ListChecks className="size-4" />
          QA 检查
          {qaCount ? (
            <span className="rounded-full bg-cinnabar-600 px-1.5 text-[10px] text-white">{qaCount}</span>
          ) : null}
        </button>
        <button
          type="button"
          className={`flex min-h-9 items-center justify-center gap-1.5 rounded-lg text-xs font-medium transition ${
            tab === "memory"
              ? "bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-950"
              : "text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          }`}
          onClick={() => setTab("memory")}
        >
          <Database className="size-3.5" />
          翻译记忆
        </button>
        <button
          type="button"
          className={`flex min-h-9 items-center justify-center gap-1 rounded-lg text-xs font-medium transition ${
            tab === "logs"
              ? "bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-950"
              : "text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800"
          }`}
          onClick={() => setTab("logs")}
        >
          <SquareTerminal className="size-3.5" />
          日志
        </button>
      </div>

      {tab === "glossary" ? (
        <>
          <div className="space-y-3 border-b hairline p-3">
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-ink-400" />
              <input
                type="search"
                className="field min-h-9 pl-8 text-xs"
                placeholder="搜索术语"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                className="btn-secondary min-h-9 px-2 py-1.5 text-xs"
                onClick={() => {
                  setEditingTerm(null);
                  setEditorOpen(true);
                }}
              >
                <Plus className="size-3.5" />
                添加
              </button>
              <button
                type="button"
                className="btn-secondary min-h-9 px-2 py-1.5 text-xs"
                onClick={() => fileInputRef.current?.click()}
                disabled={importMutation.isPending}
              >
                <Upload className="size-3.5" />
                {importMutation.isPending ? "导入中" : "CSV 导入"}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,text/csv"
                className="sr-only"
                onChange={(event) => void importCsv(event)}
              />
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {glossaryQuery.isLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 5 }).map((_, index) => (
                  <div key={index} className="h-20 animate-pulse rounded-xl bg-ink-100 dark:bg-ink-800" />
                ))}
              </div>
            ) : glossaryQuery.isError ? (
              <div className="py-10 text-center">
                <AlertCircle className="mx-auto size-5 text-cinnabar-600" />
                <p className="mt-2 text-xs text-ink-500">{errorMessage(glossaryQuery.error)}</p>
                <button type="button" className="btn-ghost mt-2 text-xs" onClick={() => void glossaryQuery.refetch()}>
                  <RefreshCw className="size-3.5" />重试
                </button>
              </div>
            ) : filteredTerms.length === 0 ? (
              <EmptyState
                compact
                icon={SpellCheck2}
                title={search ? "没有匹配术语" : "还没有术语"}
                description={search ? "换一个关键词试试。" : "添加人名、地名与专有概念，保持全书译法一致。"}
              />
            ) : (
              <ul className="space-y-2">
                {filteredTerms.map((term) => (
                  <li
                    key={term.id}
                    className={`group rounded-xl border hairline p-3 transition ${
                      term.enabled ? "bg-white dark:bg-ink-900" : "bg-ink-50 opacity-60 dark:bg-ink-950/40"
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <button
                        type="button"
                        onClick={() => toggleMutation.mutate({ id: term.id, enabled: !term.enabled })}
                        className={`mt-0.5 grid size-4 shrink-0 place-items-center rounded border ${
                          term.enabled
                            ? "border-cinnabar-700 bg-cinnabar-700 text-white"
                            : "border-ink-300 bg-white dark:border-ink-600 dark:bg-ink-900"
                        }`}
                        title={term.enabled ? "停用术语" : "启用术语"}
                        aria-label={term.enabled ? "停用术语" : "启用术语"}
                      >
                        {term.enabled ? <Check className="size-3" /> : null}
                      </button>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium text-ink-800 dark:text-ink-100">
                          {term.source_term}
                        </p>
                        <p className="mt-1 flex items-center gap-1 truncate text-xs text-cinnabar-700 dark:text-cinnabar-400">
                          <ChevronRight className="size-3 shrink-0" />
                          {term.target_term}
                        </p>
                        {term.note ? <p className="mt-1.5 line-clamp-2 text-[10px] leading-4 text-ink-400">{term.note}</p> : null}
                      </div>
                      <div className="flex opacity-70 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100">
                        <button
                          type="button"
                          className="icon-btn size-7"
                          onClick={() => {
                            setEditingTerm(term);
                            setEditorOpen(true);
                          }}
                          aria-label={`编辑 ${term.source_term}`}
                        >
                          <Pencil className="size-3" />
                        </button>
                        <button
                          type="button"
                          className="icon-btn size-7 hover:text-cinnabar-600"
                          onClick={() => setDeleteTerm(term)}
                          aria-label={`删除 ${term.source_term}`}
                        >
                          <Trash2 className="size-3" />
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button
            type="button"
            className="flex min-h-10 items-center justify-center gap-2 border-t hairline text-[11px] text-ink-500 transition hover:bg-ink-50 hover:text-ink-800 dark:hover:bg-ink-800"
            onClick={downloadCsvTemplate}
          >
            <Download className="size-3.5" />
            下载 CSV 模板
          </button>
        </>
      ) : tab === "qa" ? (
        <>
          <div className="flex items-center justify-between border-b hairline px-3 py-3">
            <div>
              <p className="text-xs font-medium text-ink-800 dark:text-ink-100">质量检查结果</p>
              <p className="mt-0.5 text-[10px] text-ink-400">检查漏译、长度、术语与占位符</p>
            </div>
            <button
              type="button"
              className="icon-btn"
              onClick={() => void qaQuery.refetch()}
              disabled={qaQuery.isFetching}
              title="重新运行 QA"
              aria-label="重新运行质量检查"
            >
              <RefreshCw className={`size-4 ${qaQuery.isFetching ? "animate-spin" : ""}`} />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {qaQuery.isLoading ? (
              <div className="flex min-h-40 items-center justify-center">
                <RefreshCw className="size-5 animate-spin text-ink-400" />
              </div>
            ) : qaQuery.isError ? (
              <div className="py-10 text-center">
                <CircleAlert className="mx-auto size-5 text-cinnabar-600" />
                <p className="mt-2 text-xs leading-5 text-ink-500">{errorMessage(qaQuery.error)}</p>
                <button type="button" className="btn-ghost mt-2 text-xs" onClick={() => void qaQuery.refetch()}>
                  重新检查
                </button>
              </div>
            ) : !qaQuery.data?.length ? (
              <EmptyState
                compact
                icon={ListChecks}
                title="没有发现问题"
                description="当前译文通过了自动质量检查。人工校对仍然值得进行。"
              />
            ) : (
              <ul className="space-y-2">
                {qaQuery.data.map((issue, index) => (
                  <li key={issue.id ?? `${issue.segment_id}-${index}`}>
                    <button
                      type="button"
                      className="group w-full rounded-xl border hairline bg-white p-3 text-left transition hover:border-ink-300 hover:shadow-sm dark:bg-ink-900 dark:hover:border-ink-700"
                      onClick={() => {
                        if (issue.segment_id) onLocate(issue.segment_id);
                        else notify("该问题没有关联到具体段落。", "info");
                      }}
                    >
                      <div className="flex items-start gap-2">
                        <span className={`badge shrink-0 ${severityStyle(issue)}`}>
                          {issue.severity === "error" ? "错误" : issue.severity === "info" ? "提示" : "警告"}
                        </span>
                        <ChevronRight className="ml-auto size-4 shrink-0 text-ink-300 transition group-hover:translate-x-0.5 group-hover:text-ink-600" />
                      </div>
                      <p className="mt-2 text-xs font-medium leading-5 text-ink-800 dark:text-ink-100">
                        {issue.message}
                      </p>
                      <p className="mt-1 text-[10px] text-ink-400">
                        {issue.code ?? issue.rule ?? issue.type ?? "质量检查"}
                        {issue.segment_id ? ` · 段落 #${issue.segment_id}` : ""}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      ) : tab === "memory" ? (
        <TranslationMemoryPanel projectId={projectId} />
      ) : (
        <RuntimeLogPanel projectId={projectId} onLocate={onLocate} />
      )}

      <TermEditor
        open={editorOpen}
        term={editingTerm}
        pending={saveMutation.isPending}
        onClose={() => {
          if (saveMutation.isPending) return;
          setEditorOpen(false);
          setEditingTerm(null);
        }}
        onSave={(input) => saveMutation.mutate(input)}
      />

      <Modal
        open={Boolean(deleteTerm)}
        onClose={() => setDeleteTerm(null)}
        title="删除术语？"
        description="后续翻译将不再使用这条固定译法。"
        size="sm"
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setDeleteTerm(null)}>取消</button>
            <button
              type="button"
              className="btn-danger"
              onClick={() => deleteTerm && deleteMutation.mutate(deleteTerm.id)}
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="size-4" />
              删除
            </button>
          </>
        }
      >
        <p className="rounded-lg bg-ink-50 p-3 text-sm dark:bg-ink-800">
          {deleteTerm?.source_term} → {deleteTerm?.target_term}
        </p>
      </Modal>
    </div>
  );
}
