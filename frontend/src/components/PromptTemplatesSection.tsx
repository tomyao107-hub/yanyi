import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, Pencil, Plus, Save, Sparkles, Star, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { PromptTemplate, PromptTemplateInput } from "../api/types";
import { useToast } from "../store/toast";
import { EmptyMessage, ServerSection, StatusPill } from "./ServerSettingsShared";

interface TemplateDraft {
  name: string;
  description: string;
  systemPrompt: string;
  userPrefix: string;
  enabled: boolean;
  isDefault: boolean;
}

const emptyTemplate: TemplateDraft = {
  name: "",
  description: "",
  systemPrompt: "你是一名专业书籍翻译。请将{source_lang}准确、自然地翻译为{target_lang}。",
  userPrefix: "",
  enabled: true,
  isDefault: false,
};

function templateToDraft(template: PromptTemplate): TemplateDraft {
  return {
    name: template.name,
    description: template.description ?? "",
    systemPrompt: template.system_prompt,
    userPrefix: template.user_prefix ?? "",
    enabled: template.enabled,
    isDefault: template.is_default,
  };
}

export function PromptTemplatesSection({
  promptPlaceholders,
}: {
  promptPlaceholders: string[];
}) {
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [draft, setDraft] = useState(emptyTemplate);
  const [previewText, setPreviewText] = useState("");

  const templatesQuery = useQuery({
    queryKey: queryKeys.promptTemplates,
    queryFn: api.promptTemplates,
  });
  const templates = templatesQuery.data ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.promptTemplates });

  const saveTemplate = useMutation({
    mutationFn: (input: PromptTemplateInput) =>
      editId === null
        ? api.createPromptTemplate(input)
        : api.updatePromptTemplate(editId, input),
    onSuccess: () => {
      void refresh();
      setDraft(emptyTemplate);
      setEditId(null);
      setFormOpen(false);
      setPreviewText("");
      notify("提示词模板已保存。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const setDefaultTemplate = useMutation({
    mutationFn: api.setDefaultPromptTemplate,
    onSuccess: () => {
      void refresh();
      notify("默认提示词已更新。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const previewTemplate = useMutation({
    mutationFn: api.previewPromptTemplate,
    onSuccess: (result) => setPreviewText(result.rendered),
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const deleteTemplate = useMutation({
    mutationFn: api.deletePromptTemplate,
    onSuccess: () => {
      void refresh();
      notify("提示词模板已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const submitTemplate = (event: FormEvent) => {
    event.preventDefault();
    saveTemplate.mutate({
      name: draft.name.trim(),
      description: draft.description.trim() || null,
      system_prompt: draft.systemPrompt.trim(),
      user_prefix: draft.userPrefix.trim() || null,
      enabled: draft.enabled,
      is_default: draft.isDefault,
    });
  };

  return (
    <ServerSection
      icon={Sparkles}
      title="翻译提示词"
      description="创建不同文体的系统提示词，默认模板会用于新书翻译。"
      action={
        <button
          type="button"
          className="btn-secondary shrink-0"
          onClick={() => {
            setDraft(emptyTemplate);
            setEditId(null);
            setFormOpen(true);
            setPreviewText("");
          }}
        >
          <Plus className="size-4" />
          新建模板
        </button>
      }
    >
      {formOpen && (
        <form onSubmit={submitTemplate} className="border-b hairline bg-ink-50/60 px-5 py-5 dark:bg-ink-950/30 sm:px-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="field-label" htmlFor="template-name">模板名称</label>
              <input
                id="template-name"
                className="field"
                value={draft.name}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, name: event.target.value }))
                }
                placeholder="例如：文学小说"
                required
              />
            </div>
            <div>
              <label className="field-label" htmlFor="template-description">说明</label>
              <input
                id="template-description"
                className="field"
                value={draft.description}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, description: event.target.value }))
                }
                placeholder="模板的适用场景"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="template-system">系统提示词</label>
              <textarea
                id="template-system"
                className="field min-h-36 resize-y"
                value={draft.systemPrompt}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, systemPrompt: event.target.value }))
                }
                required
              />
            </div>
            <div className="sm:col-span-2">
              <label className="field-label" htmlFor="template-prefix">附加要求（可选）</label>
              <textarea
                id="template-prefix"
                className="field min-h-24 resize-y"
                value={draft.userPrefix}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, userPrefix: event.target.value }))
                }
                placeholder="例如：保留章节标题、编号和专有名词。"
              />
            </div>
          </div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-ink-500">
            <span>可用占位符：</span>
            {promptPlaceholders.map((placeholder) => (
              <code key={placeholder} className="rounded bg-ink-100 px-1.5 py-0.5 dark:bg-ink-800">
                {`{${placeholder}}`}
              </code>
            ))}
          </div>
          <div className="mt-4 flex flex-col gap-3 border-t hairline pt-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap gap-4">
              <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                <input
                  type="checkbox"
                  className="size-4 accent-cinnabar-700"
                  checked={draft.enabled}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, enabled: event.target.checked }))
                  }
                />
                启用
              </label>
              <label className="flex items-center gap-2 text-sm text-ink-700 dark:text-ink-200">
                <input
                  type="checkbox"
                  className="size-4 accent-cinnabar-700"
                  checked={draft.isDefault}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, isDefault: event.target.checked }))
                  }
                />
                设为默认
              </label>
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                className="btn-secondary"
                disabled={previewTemplate.isPending || !draft.systemPrompt.trim()}
                onClick={() =>
                  previewTemplate.mutate({
                    system_prompt: draft.systemPrompt,
                    user_prefix: draft.userPrefix || null,
                    source_lang: "en",
                    target_lang: "zh-CN",
                  })
                }
              >
                <FlaskConical className="size-4" />
                预览
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => {
                  setFormOpen(false);
                  setEditId(null);
                  setPreviewText("");
                }}
              >
                取消
              </button>
              <button type="submit" className="btn-primary" disabled={saveTemplate.isPending}>
                <Save className="size-4" />
                {editId === null ? "创建模板" : "保存修改"}
              </button>
            </div>
          </div>
          {previewText && (
            <div className="mt-4 border-t hairline pt-4">
              <div className="mb-2 flex items-center gap-2 text-xs font-medium text-ink-600 dark:text-ink-300">
                <Check className="size-3.5 text-emerald-600" />
                英文 → 简体中文预览
              </div>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-ink-950 p-4 text-xs leading-6 text-ink-100">
                {previewText}
              </pre>
            </div>
          )}
        </form>
      )}
      {templatesQuery.isLoading ? (
        <EmptyMessage>正在读取提示词模板...</EmptyMessage>
      ) : templates.length === 0 ? (
        <EmptyMessage>尚未创建提示词模板。</EmptyMessage>
      ) : (
        <div className="divide-y hairline">
          {templates.map((template) => (
            <div key={template.id} className="px-5 py-4 sm:px-6">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink-900 dark:text-white">{template.name}</span>
                    {template.is_default && <StatusPill tone="success"><Star className="size-3" />默认</StatusPill>}
                    {template.is_builtin && <StatusPill tone="neutral">内置</StatusPill>}
                    {!template.enabled && <StatusPill tone="warning">已停用</StatusPill>}
                  </div>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-ink-500">
                    {template.description || template.system_prompt}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1">
                  {!template.is_default && (
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => setDefaultTemplate.mutate(template.id)}
                    >
                      <Star className="size-4" />
                      设为默认
                    </button>
                  )}
                  <button
                    type="button"
                    className="icon-btn"
                    title="编辑提示词模板"
                    aria-label={`编辑 ${template.name}`}
                    onClick={() => {
                      setDraft(templateToDraft(template));
                      setEditId(template.id);
                      setFormOpen(true);
                      setPreviewText("");
                    }}
                  >
                    <Pencil className="size-4" />
                  </button>
                  <button
                    type="button"
                    className="icon-btn text-cinnabar-600"
                    title="删除提示词模板"
                    aria-label={`删除 ${template.name}`}
                    disabled={template.is_default}
                    onClick={() => {
                      if (window.confirm(`确定删除提示词模板“${template.name}”吗？`)) {
                        deleteTemplate.mutate(template.id);
                      }
                    }}
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </ServerSection>
  );
}
