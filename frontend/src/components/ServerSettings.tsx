import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import { ModelProfilesSection } from "./ModelProfilesSection";
import { PromptTemplatesSection } from "./PromptTemplatesSection";
import { ServerCredentialsSection } from "./ServerCredentialsSection";

export function ServerSettings() {
  const runtimeQuery = useQuery({
    queryKey: queryKeys.runtimeSettings,
    queryFn: api.runtimeSettings,
    staleTime: 60_000,
  });

  const runtime = runtimeQuery.data;
  const providers = runtime?.providers ?? [];
  const suggestedModels = runtime?.suggested_models ?? [];
  const promptPlaceholders = runtime?.prompt_placeholders ?? [];
  const defaultModel = runtime?.provider_defaults?.model?.toString() ?? "";

  return (
    <div className="space-y-5">
      {runtime?.credential_key_is_ephemeral && (
        <div className="flex items-start gap-3 border-y border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200 sm:rounded-xl sm:border">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <p>当前使用临时主密钥加密凭据。重启后可能无法解密，请在服务器配置持久化主密钥。</p>
        </div>
      )}

      <ServerCredentialsSection providers={providers} />
      <ModelProfilesSection
        providers={providers}
        suggestedModels={suggestedModels}
        defaultModel={defaultModel}
        connectionTestNotice={runtime?.connection_test_notice ?? null}
      />
      <PromptTemplatesSection promptPlaceholders={promptPlaceholders} />
    </div>
  );
}
