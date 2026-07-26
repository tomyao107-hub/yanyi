import {
  BookOpenCheck,
  CircleDollarSign,
  Download,
  FileText,
  Gauge,
  Info,
  Layers3,
  Play,
  Save,
  Settings2,
  Sparkles,
} from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, errorMessage, exportUrl } from "../api/client";
import type { ExportOptions, Project, ProviderConfig } from "../api/types";
import { useSettings } from "../store/settings";
import { useToast } from "../store/toast";
import { Modal } from "./Modal";

function formatNumber(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("zh-CN").format(number) : "—";
}

export function StartTranslationDialog({
  open,
  project,
  pending,
  onClose,
  onConfirm,
}: {
  open: boolean;
  project: Project;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const estimateQuery = useQuery({
    queryKey: ["estimate", project.id, project.updated_at],
    queryFn: () => api.estimateTranslation(project.id),
    enabled: open,
    staleTime: 30_000,
    retry: false,
  });
  const estimate = estimateQuery.data;
  const tokens = estimate?.estimated_tokens ?? estimate?.estimated_total_tokens ?? (
    Number(estimate?.token_in ?? estimate?.estimated_input_tokens ?? 0) +
    Number(estimate?.token_out ?? estimate?.estimated_output_tokens ?? 0)
  );
  const cost = Number(estimate?.estimated_cost ?? estimate?.estimated_cost_usd);
  const currency = String(estimate?.currency ?? "USD").toUpperCase();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={project.status === "stopped" ? "继续翻译" : "开始翻译"}
      description="引擎只处理待翻译与出错段落；已有译文和已校对段落不会重复调用模型。"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={pending}>取消</button>
          <button type="button" className="btn-primary" onClick={onConfirm} disabled={pending}>
            {pending ? (
              <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : (
              <Play className="size-4 fill-current" />
            )}
            {pending ? "正在启动" : "确认开始"}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border hairline bg-ink-50/70 p-4 dark:bg-ink-950/30">
          <div className="flex items-center gap-2 text-xs text-ink-500">
            <Sparkles className="size-3.5" />
            当前模型
          </div>
          <p className="mt-2 truncate font-mono text-sm font-medium text-ink-900 dark:text-white" title={String(project.provider_cfg.model ?? "")}>
            {String(project.provider_cfg.model ?? "后端默认模型")}
          </p>
        </div>
        <div className="rounded-xl border hairline bg-ink-50/70 p-4 dark:bg-ink-950/30">
          <div className="flex items-center gap-2 text-xs text-ink-500">
            <Layers3 className="size-3.5" />
            待处理段落
          </div>
          <p className="mt-2 font-mono text-lg font-semibold tabular-nums text-ink-900 dark:text-white">
            {estimateQuery.isLoading
              ? "…"
              : formatNumber(estimate?.pending_segments ?? estimate?.remaining_segments ?? estimate?.segments ?? project.stats?.pending)}
          </p>
        </div>
      </div>

      <div className="mt-3 rounded-xl border hairline p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-ink-800 dark:text-ink-100">
            <CircleDollarSign className="size-4 text-cinnabar-600" />
            成本估算
          </div>
          {estimateQuery.isFetching ? (
            <span className="text-xs text-ink-400">正在估算…</span>
          ) : null}
        </div>
        {estimateQuery.isError ? (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
            暂时无法取得成本估算，但不影响启动翻译。{errorMessage(estimateQuery.error)}
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-2 gap-3">
            <div>
              <span className="block text-[10px] uppercase tracking-wider text-ink-400">预计 tokens</span>
              <span className="mt-1 block font-mono text-sm font-medium tabular-nums">
                {estimateQuery.isLoading ? "—" : formatNumber(tokens)}
              </span>
            </div>
            <div>
              <span className="block text-[10px] uppercase tracking-wider text-ink-400">预计费用</span>
              <span className="mt-1 block font-mono text-sm font-medium tabular-nums">
                {Number.isFinite(cost) ? `${currency} ${cost.toFixed(cost < 1 ? 4 : 2)}` : "以服务商账单为准"}
              </span>
            </div>
          </div>
        )}
      </div>
      <p className="mt-4 flex items-start gap-2 text-xs leading-5 text-ink-500">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        实际消耗取决于上下文长度、模型输出和翻译记忆命中率。可随时安全停止，已完成段落会保留。
      </p>
    </Modal>
  );
}

export function ExportDialog({
  open,
  project,
  onClose,
}: {
  open: boolean;
  project: Project;
  onClose: () => void;
}) {
  const { settings } = useSettings();
  const { notify } = useToast();
  const [options, setOptions] = useState<ExportOptions>({
    mode: settings.exportMode,
    include_untranslated: settings.includeUntranslated,
    format: project.source_type.toLowerCase() === "epub" ? "epub" : "md",
  });

  useEffect(() => {
    if (!open) return;
    setOptions({
      mode: settings.exportMode,
      include_untranslated: settings.includeUntranslated,
      format: project.source_type.toLowerCase() === "epub" ? "epub" : "md",
    });
  }, [open, project.source_type, settings.exportMode, settings.includeUntranslated]);

  const exportMutation = useMutation({
    mutationFn: () => api.createExport(project.id, options),
    onSuccess: (result) => {
      const url = exportUrl(result);
      if (url) {
        const anchor = document.createElement("a");
        anchor.href = url;
        if (result.filename) anchor.download = result.filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        notify("导出文件已生成，下载即将开始。", "success");
        onClose();
      } else {
        notify("文件已生成，但后端没有返回下载地址。", "info");
      }
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  return (
    <Modal
      open={open}
      onClose={() => !exportMutation.isPending && onClose()}
      title="导出译本"
      description="原文件的章节、样式、图片与代码结构会被保留。"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={exportMutation.isPending}>
            取消
          </button>
          <button
            type="button"
            className="btn-primary min-w-28"
            onClick={() => exportMutation.mutate()}
            disabled={exportMutation.isPending}
          >
            {exportMutation.isPending ? (
              <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : (
              <Download className="size-4" />
            )}
            {exportMutation.isPending ? "生成中" : "生成文件"}
          </button>
        </>
      }
    >
      <div>
        <span className="field-label">内容模式</span>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {([
            ["bilingual", "双语对照", "原文与译文逐段相邻，适合阅读与校订。", BookOpenCheck],
            ["target_only", "仅保留译文", "生成更接近正式出版物的纯译文版本。", FileText],
          ] as const).map(([value, label, description, Icon]) => (
            <button
              key={value}
              type="button"
              className={`rounded-xl border p-4 text-left transition ${
                options.mode === value
                  ? "border-cinnabar-500 bg-cinnabar-50/70 dark:bg-cinnabar-950/30"
                  : "border-ink-200 hover:border-ink-300 hover:bg-ink-50 dark:border-ink-700 dark:hover:bg-ink-800"
              }`}
              onClick={() => setOptions((current) => ({ ...current, mode: value }))}
            >
              <Icon className={`size-5 ${options.mode === value ? "text-cinnabar-600" : "text-ink-400"}`} />
              <span className="mt-3 block text-sm font-medium text-ink-900 dark:text-white">{label}</span>
              <span className="mt-1 block text-xs leading-5 text-ink-500">{description}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label className="field-label" htmlFor="export-format">文件格式（随源文件）</label>
          <select
            id="export-format"
            className="field"
            value={options.format}
            disabled
          >
            {project.source_type.toLowerCase() === "epub" ? (
              <option value="epub">EPUB 电子书</option>
            ) : (
              <option value="md">Markdown 文档</option>
            )}
          </select>
        </div>
        <div>
          <span className="field-label">未翻译段落</span>
          <label className="flex min-h-10 cursor-pointer items-center justify-between rounded-lg border hairline px-3">
            <span className="text-sm text-ink-700 dark:text-ink-200">保留原文</span>
            <input
              type="checkbox"
              checked={options.include_untranslated}
              onChange={(event) =>
                setOptions((current) => ({ ...current, include_untranslated: event.target.checked }))
              }
              className="size-4 accent-cinnabar-700"
            />
          </label>
        </div>
      </div>
    </Modal>
  );
}

export function ProjectConfigDialog({
  open,
  project,
  pending,
  onClose,
  onSave,
}: {
  open: boolean;
  project: Project;
  pending: boolean;
  onClose: () => void;
  onSave: (config: ProviderConfig) => void;
}) {
  const [draft, setDraft] = useState<ProviderConfig>(project.provider_cfg);

  useEffect(() => {
    if (open) setDraft(project.provider_cfg);
  }, [open, project.provider_cfg]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSave({
      ...draft,
      generate_chapter_summaries: draft.generate_chapter_summaries ?? true,
      stream: draft.stream ?? true,
    });
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="当前书目设置"
      description="修改只作用于这本书，下一次启动翻译时生效。"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={pending}>取消</button>
          <button type="submit" form="project-config-form" className="btn-primary" disabled={pending}>
            <Save className="size-4" />
            {pending ? "保存中" : "保存设置"}
          </button>
        </>
      }
    >
      <form id="project-config-form" className="space-y-5" onSubmit={submit}>
        <div>
          <label className="field-label" htmlFor="project-model">模型</label>
          <div className="relative">
            <Settings2 className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-400" />
            <input
              id="project-model"
              className="field pl-9 font-mono"
              value={String(draft.model ?? "")}
              onChange={(event) => setDraft((current) => ({ ...current, model: event.target.value }))}
              required
            />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="field-label" htmlFor="project-temperature">温度</label>
            <input
              id="project-temperature"
              type="number"
              min={0}
              max={2}
              step={0.1}
              className="field"
              value={Number(draft.temperature ?? 0.3)}
              onChange={(event) => setDraft((current) => ({ ...current, temperature: Number(event.target.value) }))}
            />
          </div>
          <div>
            <label className="field-label" htmlFor="project-concurrency">最大并发</label>
            <input
              id="project-concurrency"
              type="number"
              min={1}
              max={32}
              className="field"
              value={Number(draft.max_concurrency ?? 4)}
              onChange={(event) => setDraft((current) => ({ ...current, max_concurrency: Number(event.target.value) }))}
            />
          </div>
          <div>
            <label className="field-label" htmlFor="project-budget">上下文预算</label>
            <input
              id="project-budget"
              type="number"
              min={400}
              step={100}
              className="field"
              value={Number(draft.context_token_budget ?? draft.context_budget ?? 3200)}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  context_token_budget: Number(event.target.value),
                }))
              }
            />
          </div>
          <div>
            <label className="field-label" htmlFor="project-context">前文段数</label>
            <input
              id="project-context"
              type="number"
              min={0}
              max={12}
              className="field"
              value={Number(draft.context_segments ?? 3)}
              onChange={(event) => setDraft((current) => ({ ...current, context_segments: Number(event.target.value) }))}
            />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex cursor-pointer items-center justify-between rounded-xl border hairline p-3">
            <span>
              <span className="block text-sm font-medium text-ink-800 dark:text-ink-100">生成章节摘要</span>
              <span className="mt-0.5 block text-[10px] text-ink-500">增强长篇上下文一致性</span>
            </span>
            <input
              type="checkbox"
              checked={Boolean(draft.generate_chapter_summaries ?? true)}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  generate_chapter_summaries: event.target.checked,
                }))
              }
              className="size-4 accent-cinnabar-700"
            />
          </label>
          <label className="flex cursor-pointer items-center justify-between rounded-xl border hairline p-3">
            <span>
              <span className="block text-sm font-medium text-ink-800 dark:text-ink-100">流式显示译文</span>
              <span className="mt-0.5 block text-[10px] text-ink-500">通过 SSE 逐步显示结果</span>
            </span>
            <input
              type="checkbox"
              checked={Boolean(draft.stream ?? true)}
              onChange={(event) =>
                setDraft((current) => ({ ...current, stream: event.target.checked }))
              }
              className="size-4 accent-cinnabar-700"
            />
          </label>
        </div>
        <div className="flex items-start gap-2 rounded-lg bg-ink-50 p-3 text-xs leading-5 text-ink-500 dark:bg-ink-800/60">
          <Gauge className="mt-0.5 size-3.5 shrink-0" />
          增加上下文可提高长篇一致性，也会增加输入 token 与成本。
        </div>
      </form>
    </Modal>
  );
}
