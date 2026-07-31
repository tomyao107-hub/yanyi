import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronUp, KeyRound, Plus, RefreshCw, Save, ShieldCheck, Trash2, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, errorMessage } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { ProviderOption, ProviderCredential, ProviderCredentialInput } from "../api/types";
import { useToast } from "../store/toast";
import { EmptyMessage, ServerSection, StatusPill } from "./ServerSettingsShared";

const emptyCredential: ProviderCredentialInput = {
  provider: "custom",
  profile_label: "",
  api_key: "",
};

export function ServerCredentialsSection({ providers }: { providers: ProviderOption[] }) {
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [formOpen, setFormOpen] = useState(false);
  const [draft, setDraft] = useState(emptyCredential);
  const [rotateId, setRotateId] = useState<number | null>(null);
  const [rotateKey, setRotateKey] = useState("");

  const credentialsQuery = useQuery({
    queryKey: queryKeys.providerCredentials,
    queryFn: api.providerCredentials,
  });
  const credentials = credentialsQuery.data ?? [];
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.providerCredentials });

  const createCredential = useMutation({
    mutationFn: api.createProviderCredential,
    onSuccess: () => {
      void refresh();
      setDraft(emptyCredential);
      setFormOpen(false);
      notify("API Key 已加密保存。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const rotateCredential = useMutation({
    mutationFn: ({ id, apiKey }: { id: number; apiKey: string }) =>
      api.rotateProviderCredential(id, apiKey),
    onSuccess: () => {
      void refresh();
      setRotateId(null);
      setRotateKey("");
      notify("API Key 已轮换。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });
  const deleteCredential = useMutation({
    mutationFn: api.deleteProviderCredential,
    onSuccess: () => {
      void refresh();
      notify("凭据已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  return (
    <ServerSection
      icon={KeyRound}
      title="供应商凭据"
      description="API Key 在服务端加密保存，读取接口和浏览器都不会得到明文。"
      action={
        <button
          type="button"
          className="btn-secondary shrink-0"
          onClick={() => setFormOpen((value) => !value)}
        >
          {formOpen ? <ChevronUp className="size-4" /> : <Plus className="size-4" />}
          {formOpen ? "收起" : "添加凭据"}
        </button>
      }
    >
      {formOpen && (
        <form
          className="grid gap-4 border-b hairline bg-ink-50/60 px-5 py-5 dark:bg-ink-950/30 sm:grid-cols-2 sm:px-6"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            createCredential.mutate({
              ...draft,
              profile_label: draft.profile_label.trim(),
              api_key: draft.api_key.trim(),
            });
          }}
        >
          <div>
            <label className="field-label" htmlFor="credential-provider">供应商</label>
            <select
              id="credential-provider"
              className="field"
              value={draft.provider}
              onChange={(event) => setDraft((current) => ({ ...current, provider: event.target.value }))}
            >
              {providers.map((provider) => (
                <option key={provider.name} value={provider.name}>{provider.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="credential-label">凭据名称</label>
            <input
              id="credential-label"
              className="field"
              value={draft.profile_label}
              onChange={(event) =>
                setDraft((current) => ({ ...current, profile_label: event.target.value }))
              }
              placeholder="例如：国内中转"
              required
            />
          </div>
          <div className="sm:col-span-2">
            <label className="field-label" htmlFor="credential-key">API Key</label>
            <input
              id="credential-key"
              type="password"
              autoComplete="new-password"
              className="field font-mono"
              value={draft.api_key}
              onChange={(event) =>
                setDraft((current) => ({ ...current, api_key: event.target.value }))
              }
              placeholder="输入后将立即加密，之后不可查看明文"
              required
            />
          </div>
          <div className="flex justify-end gap-2 sm:col-span-2">
            <button type="button" className="btn-ghost" onClick={() => setFormOpen(false)}>
              取消
            </button>
            <button type="submit" className="btn-primary" disabled={createCredential.isPending}>
              <ShieldCheck className="size-4" />
              加密保存
            </button>
          </div>
        </form>
      )}
      {credentialsQuery.isLoading ? (
        <EmptyMessage>正在读取凭据...</EmptyMessage>
      ) : credentials.length === 0 ? (
        <EmptyMessage>尚未保存服务端凭据。</EmptyMessage>
      ) : (
        <div className="divide-y hairline">
          {credentials.map((credential: ProviderCredential) => (
            <div key={credential.id} className="px-5 py-4 sm:px-6">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-ink-900 dark:text-white">
                      {credential.profile_label}
                    </span>
                    <StatusPill tone="neutral">{credential.provider}</StatusPill>
                    <StatusPill
                      tone={
                        credential.test_status === "valid"
                          ? "success"
                          : credential.test_status === "invalid"
                            ? "warning"
                            : "neutral"
                      }
                    >
                      {credential.test_status === "valid"
                        ? "已验证"
                        : credential.test_status === "invalid"
                          ? "验证失败"
                          : "未验证"}
                    </StatusPill>
                  </div>
                  <p className="mt-1 font-mono text-xs text-ink-500">{credential.masked_key}</p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => {
                      setRotateId(credential.id);
                      setRotateKey("");
                    }}
                  >
                    <RefreshCw className="size-4" />
                    轮换
                  </button>
                  <button
                    type="button"
                    className="icon-btn text-cinnabar-600"
                    title="删除凭据"
                    aria-label={`删除凭据 ${credential.profile_label}`}
                    onClick={() => {
                      if (window.confirm(`确定删除凭据“${credential.profile_label}”吗？`)) {
                        deleteCredential.mutate(credential.id);
                      }
                    }}
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </div>
              {rotateId === credential.id && (
                <form
                  className="mt-4 flex flex-col gap-2 sm:flex-row"
                  onSubmit={(event) => {
                    event.preventDefault();
                    rotateCredential.mutate({ id: credential.id, apiKey: rotateKey.trim() });
                  }}
                >
                  <input
                    type="password"
                    autoComplete="new-password"
                    className="field flex-1 font-mono"
                    value={rotateKey}
                    onChange={(event) => setRotateKey(event.target.value)}
                    placeholder="输入新的 API Key"
                    aria-label={`轮换 ${credential.profile_label} 的 API Key`}
                    required
                  />
                  <button type="submit" className="btn-primary" disabled={rotateCredential.isPending}>
                    <Save className="size-4" />
                    保存新密钥
                  </button>
                  <button type="button" className="icon-btn" onClick={() => setRotateId(null)}>
                    <X className="size-4" />
                    <span className="sr-only">取消轮换</span>
                  </button>
                </form>
              )}
            </div>
          ))}
        </div>
      )}
    </ServerSection>
  );
}
