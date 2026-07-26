import {
  Check,
  CheckCircle2,
  Copy,
  Pencil,
  RotateCw,
  Save,
  Undo2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Segment } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { useToast } from "../store/toast";

interface SegmentRowProps {
  segment: Segment;
  selected: boolean;
  active: boolean;
  saving: boolean;
  retranslating: boolean;
  shortcutsEnabled: boolean;
  onSelect: (checked: boolean) => void;
  onActivate: () => void;
  onSave: (targetText: string) => Promise<void>;
  onReview: () => void;
  onRetranslate: () => void;
}

export function SegmentRow({
  segment,
  selected,
  active,
  saving,
  retranslating,
  shortcutsEnabled,
  onSelect,
  onActivate,
  onSave,
  onReview,
  onRetranslate,
}: SegmentRowProps) {
  const { notify } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(segment.target_text ?? "");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const processing = segment.status === "processing" || retranslating;

  useEffect(() => {
    if (!editing) setDraft(segment.target_text ?? "");
  }, [editing, segment.target_text]);

  useEffect(() => {
    if (processing) setEditing(false);
  }, [processing]);

  useEffect(() => {
    if (editing) {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(draft.length, draft.length);
    }
  }, [editing]);

  const dirty = draft !== (segment.target_text ?? "");
  const save = async () => {
    if (processing) {
      notify("该段正在翻译，完成后才能编辑。", "error");
      return;
    }
    await onSave(draft);
    setEditing(false);
  };

  const copy = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      notify(`${label}已复制。`, "success");
    } catch {
      notify("浏览器未允许访问剪贴板。", "error");
    }
  };

  return (
    <article
      id={`segment-${segment.id}`}
      tabIndex={-1}
      className={`surface group h-full overflow-hidden rounded-xl transition ${
        active ? "border-cinnabar-300 ring-1 ring-cinnabar-200 dark:border-cinnabar-800 dark:ring-cinnabar-900" : ""
      }`}
      onMouseDown={onActivate}
      onFocusCapture={onActivate}
      aria-label={`第 ${segment.ord + 1} 段，${segment.status}`}
    >
      <header className="flex min-h-11 items-center gap-2 border-b hairline bg-ink-50/70 px-3 dark:bg-ink-950/35 sm:px-4">
        <label className="grid size-6 cursor-pointer place-items-center" title="选择此段">
          <input
            type="checkbox"
            className="size-4 rounded border-ink-300 accent-cinnabar-700"
            checked={selected}
            onChange={(event) => onSelect(event.target.checked)}
            disabled={processing}
            aria-label={`选择第 ${segment.ord + 1} 段`}
          />
        </label>
        <span className="font-mono text-[11px] tabular-nums text-ink-400">
          § {String(segment.ord + 1).padStart(4, "0")}
        </span>
        {segment.chapter_title ? (
          <span className="hidden min-w-0 truncate text-[11px] text-ink-500 sm:block">
            {segment.chapter_title}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          {segment.provider ? (
            <span className="hidden max-w-36 truncate text-[10px] text-ink-400 md:block" title={segment.provider}>
              {segment.provider}
            </span>
          ) : null}
          <StatusBadge status={retranslating ? "processing" : segment.status} />
        </div>
      </header>

      <div className="grid min-h-[180px] grid-cols-1 lg:grid-cols-2">
        <section className="relative border-b hairline p-4 lg:border-b-0 lg:border-r sm:p-5" aria-label="原文">
          <div className="mb-3 flex items-center justify-between">
            <span className="eyebrow !text-ink-400">Source</span>
            <button
              type="button"
              className="icon-btn -m-2 size-8 opacity-60 sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100"
              onClick={() => void copy(segment.source_text, "原文")}
              title="复制原文"
              aria-label="复制原文"
            >
              <Copy className="size-3.5" />
            </button>
          </div>
          <p className="whitespace-pre-wrap font-serif text-[15px] leading-7 text-ink-800 dark:text-ink-100">
            {segment.source_text}
          </p>
        </section>

        <section
          className={`relative p-4 sm:p-5 ${
            segment.status === "error" ? "bg-cinnabar-50/30 dark:bg-cinnabar-950/10" : ""
          }`}
          aria-label="译文"
        >
          <div className="mb-3 flex min-h-6 items-center justify-between gap-2">
            <span className="eyebrow">Translation</span>
            <div className="flex items-center gap-0.5">
              {editing ? (
                <>
                  <button
                    type="button"
                    className="icon-btn size-8"
                    onClick={() => {
                      setDraft(segment.target_text ?? "");
                      setEditing(false);
                    }}
                    disabled={saving}
                    title="取消编辑"
                    aria-label="取消编辑"
                  >
                    <Undo2 className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className="icon-btn size-8 text-cinnabar-700 dark:text-cinnabar-400"
                    onClick={() => void save()}
                    disabled={saving || !dirty}
                    title="保存译文（Ctrl + Enter）"
                    aria-label="保存译文"
                  >
                    {saving ? (
                      <span className="size-3.5 animate-spin rounded-full border-2 border-ink-300 border-t-cinnabar-600" />
                    ) : (
                      <Save className="size-3.5" />
                    )}
                  </button>
                </>
              ) : (
                <>
                  {segment.target_text ? (
                    <button
                      type="button"
                      className="icon-btn size-8 opacity-60 sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100"
                      onClick={() => void copy(segment.target_text ?? "", "译文")}
                      title="复制译文"
                      aria-label="复制译文"
                    >
                      <Copy className="size-3.5" />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="icon-btn size-8 opacity-70 sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100"
                    onClick={() => setEditing(true)}
                    disabled={processing}
                    title={processing ? "翻译完成后可编辑" : "编辑译文"}
                    aria-label="编辑译文"
                  >
                    <Pencil className="size-3.5" />
                  </button>
                  <button
                    type="button"
                    className={`icon-btn size-8 ${
                      segment.status === "reviewed" ? "text-violet-600 dark:text-violet-400" : ""
                    }`}
                    onClick={onReview}
                    disabled={!segment.target_text || saving || processing}
                    title={segment.status === "reviewed" ? "取消已校对" : "标记为已校对（R）"}
                    aria-label={segment.status === "reviewed" ? "取消已校对" : "标记为已校对"}
                  >
                    {segment.status === "reviewed" ? <Check className="size-4" /> : <CheckCircle2 className="size-4" />}
                  </button>
                  <button
                    type="button"
                    className="icon-btn size-8"
                    onClick={onRetranslate}
                    disabled={processing}
                    title="重新翻译（T）"
                    aria-label="重新翻译此段"
                  >
                    <RotateCw className={`size-4 ${retranslating ? "animate-spin" : ""}`} />
                  </button>
                </>
              )}
            </div>
          </div>

          {editing ? (
            <>
              <textarea
                ref={textareaRef}
                className="field min-h-36 resize-y font-serif text-[15px] leading-7"
                value={draft}
                disabled={processing}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    shortcutsEnabled &&
                    !document.querySelector('[role="dialog"][aria-modal="true"]') &&
                    event.key === "Enter" &&
                    (event.ctrlKey || event.metaKey)
                  ) {
                    event.preventDefault();
                    if (dirty && !saving) void save();
                  }
                  if (event.key === "Escape") {
                    setDraft(segment.target_text ?? "");
                    setEditing(false);
                  }
                }}
                aria-label={`第 ${segment.ord + 1} 段译文`}
              />
              <p className="mt-2 text-[10px] text-ink-400">
                {shortcutsEnabled ? "Ctrl / ⌘ + Enter 保存 · " : ""}
                Esc 取消
              </p>
            </>
          ) : segment.target_text ? (
            <button
              type="button"
              className="block w-full cursor-text rounded-md text-left"
              onDoubleClick={() => setEditing(true)}
              title="双击编辑"
            >
              <span className="whitespace-pre-wrap font-serif text-[15px] leading-7 text-ink-900 dark:text-ink-50">
                {segment.target_text}
              </span>
            </button>
          ) : retranslating || segment.status === "processing" ? (
            <div className="flex min-h-24 items-center gap-3 text-sm text-ink-400">
              <span className="size-4 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600 dark:border-blue-900 dark:border-t-blue-400" />
              模型正在翻译这一段…
            </div>
          ) : (
            <button
              type="button"
              className="flex min-h-24 w-full items-center justify-center rounded-lg border border-dashed border-ink-200 text-sm text-ink-400 transition hover:border-ink-300 hover:bg-ink-50 dark:border-ink-700 dark:hover:bg-ink-800/40"
              onClick={() => setEditing(true)}
            >
              点击录入译文
            </button>
          )}

          {segment.error_msg ? (
            <p className="mt-3 rounded-lg bg-cinnabar-50 px-3 py-2 text-xs leading-5 text-cinnabar-700 dark:bg-cinnabar-950/35 dark:text-cinnabar-300">
              {segment.error_msg}
            </p>
          ) : null}
        </section>
      </div>
    </article>
  );
}
