import {
  Braces,
  Check,
  Command,
  Cpu,
  Download,
  Info,
  Keyboard,
  Moon,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Sun,
  Type,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  type AppSettings,
  type ThemePreference,
  useSettings,
} from "../store/settings";
import { useToast } from "../store/toast";

const suggestedModels = [
  "openai/gemini-3.1-pro-low",
  "deepseek/deepseek-v4-flash",
  "openai/gpt-5-mini",
  "openai/gpt-5.4-mini",
  "anthropic/claude-sonnet-5",
  "gemini/gemini-3.6-flash",
  "gemini/gemini-3.5-flash-lite",
  "ollama/qwen3",
];

function SettingSection({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: typeof Cpu;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="surface rounded-2xl">
      <div className="flex items-start gap-3 border-b hairline px-5 py-4 sm:px-6">
        <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300">
          <Icon className="size-4" />
        </span>
        <div>
          <h2 className="font-serif text-lg font-semibold text-ink-950 dark:text-white">{title}</h2>
          <p className="mt-0.5 text-xs leading-5 text-ink-500 dark:text-ink-400">{description}</p>
        </div>
      </div>
      <div className="p-5 sm:p-6">{children}</div>
    </section>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  min,
  max,
  step = 1,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <label className="field-label mb-0" htmlFor={id}>{label}</label>
        <span className="rounded-md bg-ink-100 px-2 py-0.5 font-mono text-xs tabular-nums text-ink-700 dark:bg-ink-800 dark:text-ink-200">
          {value}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="mt-3 h-2 w-full cursor-pointer appearance-none rounded-full bg-ink-200 accent-cinnabar-700 dark:bg-ink-700"
      />
      <p className="mt-2 text-xs leading-5 text-ink-500">{hint}</p>
    </div>
  );
}

export function SettingsPage() {
  const { settings, replaceSettings, resetSettings } = useSettings();
  const { notify } = useToast();
  const [draft, setDraft] = useState<AppSettings>(settings);
  const [saved, setSaved] = useState(false);

  useEffect(() => setDraft(settings), [settings]);

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(settings), [draft, settings]);
  const update = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    replaceSettings(draft);
    setSaved(true);
    notify("默认设置已保存在本机。", "success");
    window.setTimeout(() => setSaved(false), 1800);
  };

  const reset = () => {
    setDraft(resetSettings());
    notify("已恢复默认设置。", "info");
  };

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">
      <div className="flex flex-col gap-5 border-b hairline pb-8 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow">Studio preferences</p>
          <h1 className="mt-2 font-serif text-3xl font-semibold tracking-tight text-ink-950 dark:text-white sm:text-4xl">
            工作台设置
          </h1>
          <p className="mt-3 max-w-xl text-sm leading-6 text-ink-500 dark:text-ink-400">
            这里保存新书目的默认参数。已有书目可在翻译工作台中单独调整模型。
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={reset}>
            <RotateCcw className="size-4" />
            恢复默认
          </button>
          <button type="submit" form="settings-form" className="btn-primary min-w-28" disabled={!dirty}>
            {saved ? <Check className="size-4" /> : <Save className="size-4" />}
            {saved ? "已保存" : "保存设置"}
          </button>
        </div>
      </div>

      <form id="settings-form" onSubmit={submit} className="mt-8 space-y-5">
        <SettingSection
          icon={Cpu}
          title="翻译模型"
          description="使用 LiteLLM 模型命名；密钥由后端环境变量读取，不会进入浏览器。"
        >
          <div>
            <label className="field-label" htmlFor="default-model">默认模型</label>
            <input
              id="default-model"
              list="model-suggestions"
              className="field font-mono"
              value={draft.model}
              onChange={(event) => update("model", event.target.value)}
              placeholder="provider/model"
              required
            />
            <datalist id="model-suggestions">
              {suggestedModels.map((model) => <option key={model} value={model} />)}
            </datalist>
            <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-ink-500">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              例如 deepseek/deepseek-v4-flash、openai/gpt-5-mini 或 ollama/qwen3。
            </p>
          </div>
        </SettingSection>

        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <SettingSection
            icon={SlidersHorizontal}
            title="生成参数"
            description="控制译文的稳定性与任务并发。"
          >
            <div className="space-y-7">
              <NumberField
                id="temperature"
                label="温度"
                hint="低温度更稳定一致；文学性较强的作品可适度提高。"
                value={draft.temperature}
                min={0}
                max={1}
                step={0.1}
                onChange={(value) => update("temperature", value)}
              />
              <NumberField
                id="concurrency"
                label="最大并发"
                hint="过高可能触发服务商限流。本地模型建议从 1–2 开始。"
                value={draft.maxConcurrency}
                min={1}
                max={16}
                onChange={(value) => update("maxConcurrency", value)}
              />
              <div className="space-y-2">
                <label className="flex cursor-pointer items-center justify-between rounded-xl border hairline p-3">
                  <span>
                    <span className="block text-sm font-medium text-ink-800 dark:text-ink-100">生成章节摘要</span>
                    <span className="mt-0.5 block text-[11px] text-ink-500">为后续段落提供全章语境</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={draft.generateChapterSummaries}
                    onChange={(event) => update("generateChapterSummaries", event.target.checked)}
                    className="size-4 accent-cinnabar-700"
                  />
                </label>
                <label className="flex cursor-pointer items-center justify-between rounded-xl border hairline p-3">
                  <span>
                    <span className="block text-sm font-medium text-ink-800 dark:text-ink-100">流式显示译文</span>
                    <span className="mt-0.5 block text-[11px] text-ink-500">模型生成时逐字更新工作台</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={draft.streamTranslation}
                    onChange={(event) => update("streamTranslation", event.target.checked)}
                    className="size-4 accent-cinnabar-700"
                  />
                </label>
              </div>
            </div>
          </SettingSection>

          <SettingSection
            icon={Braces}
            title="上下文窗口"
            description="用于保持人名、概念与叙述语气一致。"
          >
            <div className="space-y-7">
              <NumberField
                id="context-budget"
                label="上下文预算"
                hint="术语、前文译文和章节摘要共同使用此 token 预算。"
                value={draft.contextBudget}
                min={800}
                max={10000}
                step={200}
                onChange={(value) => update("contextBudget", value)}
              />
              <NumberField
                id="context-segments"
                label="携带前文段数"
                hint="通常 2–4 段足以保持代词与语气衔接。"
                value={draft.contextSegments}
                min={0}
                max={8}
                onChange={(value) => update("contextSegments", value)}
              />
            </div>
          </SettingSection>
        </div>

        <SettingSection
          icon={Download}
          title="导出默认值"
          description="导出时仍可针对当前书目临时修改。"
        >
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
            <div>
              <span className="field-label">内容模式</span>
              <div className="grid grid-cols-2 gap-2">
                {([
                  ["bilingual", "双语对照"],
                  ["target_only", "仅译文"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={`min-h-11 rounded-lg border px-3 text-sm font-medium transition ${
                      draft.exportMode === value
                        ? "border-cinnabar-600 bg-cinnabar-50 text-cinnabar-800 dark:bg-cinnabar-950/40 dark:text-cinnabar-300"
                        : "border-ink-200 bg-white text-ink-600 hover:bg-ink-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-300"
                    }`}
                    onClick={() => update("exportMode", value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <span className="field-label">未翻译段落</span>
              <label className="flex min-h-11 cursor-pointer items-center justify-between rounded-lg border border-ink-200 bg-white px-3 dark:border-ink-700 dark:bg-ink-900">
                <span className="text-sm text-ink-700 dark:text-ink-200">保留原文</span>
                <input
                  type="checkbox"
                  className="size-4 rounded accent-cinnabar-700"
                  checked={draft.includeUntranslated}
                  onChange={(event) => update("includeUntranslated", event.target.checked)}
                />
              </label>
            </div>
          </div>
        </SettingSection>

        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <SettingSection
            icon={Type}
            title="界面外观"
            description="跟随系统可自动切换日间与夜间阅读环境。"
          >
            <div className="grid grid-cols-3 gap-2">
              {([
                ["light", "浅色", Sun],
                ["dark", "深色", Moon],
                ["system", "跟随系统", Command],
              ] as const).map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  className={`flex min-h-20 flex-col items-center justify-center gap-2 rounded-xl border text-xs font-medium transition ${
                    draft.theme === value
                      ? "border-cinnabar-600 bg-cinnabar-50 text-cinnabar-800 dark:bg-cinnabar-950/40 dark:text-cinnabar-300"
                      : "border-ink-200 bg-white text-ink-600 hover:bg-ink-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-300"
                  }`}
                  onClick={() => update("theme", value as ThemePreference)}
                  aria-pressed={draft.theme === value}
                >
                  <Icon className="size-4" />
                  {label}
                </button>
              ))}
            </div>
          </SettingSection>

          <SettingSection
            icon={Keyboard}
            title="键盘操作"
            description="在校订长篇文本时减少鼠标往返。"
          >
            <label className="flex cursor-pointer items-center justify-between rounded-xl border border-ink-200 bg-white p-3 dark:border-ink-700 dark:bg-ink-900">
              <span>
                <span className="block text-sm font-medium text-ink-800 dark:text-ink-100">启用快捷键</span>
                <span className="mt-0.5 block text-xs text-ink-500">输入框获得焦点时仅保留保存快捷键</span>
              </span>
              <input
                type="checkbox"
                checked={draft.shortcutsEnabled}
                onChange={(event) => update("shortcutsEnabled", event.target.checked)}
                className="size-4 accent-cinnabar-700"
              />
            </label>
            <dl className="mt-4 grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-xs text-ink-500">
              <dt>保存当前译文</dt><dd><kbd className="rounded border hairline bg-ink-50 px-1.5 py-0.5 font-mono dark:bg-ink-800">Ctrl ↵</kbd></dd>
              <dt>下一段 / 上一段</dt><dd><kbd className="rounded border hairline bg-ink-50 px-1.5 py-0.5 font-mono dark:bg-ink-800">J / K</kbd></dd>
              <dt>标记已校对</dt><dd><kbd className="rounded border hairline bg-ink-50 px-1.5 py-0.5 font-mono dark:bg-ink-800">R</kbd></dd>
              <dt>重新翻译</dt><dd><kbd className="rounded border hairline bg-ink-50 px-1.5 py-0.5 font-mono dark:bg-ink-800">T</kbd></dd>
            </dl>
          </SettingSection>
        </div>
      </form>
    </div>
  );
}
