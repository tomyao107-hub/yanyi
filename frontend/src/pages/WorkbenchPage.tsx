import {
  ArrowLeft,
  CheckCheck,
  ChevronDown,
  Download,
  Filter,
  Layers3,
  PanelRightClose,
  PanelRightOpen,
  Pause,
  Play,
  RefreshCw,
  RotateCw,
  Settings2,
  Square,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type QueryKey,
} from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useNavigate, useParams } from "react-router-dom";
import { api, errorMessage, projectProgress } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type {
  ProviderConfig,
  Segment,
  SegmentPage,
  StreamEvent,
  TranslateScope,
} from "../api/types";
import { useProjectStream } from "../api/useProjectStream";
import { EmptyState } from "../components/EmptyState";
import {
  ExportDialog,
  ProjectConfigDialog,
  StartTranslationDialog,
} from "../components/WorkbenchDialogs";
import { WorkbenchSidebar } from "../components/WorkbenchSidebar";
import { Modal } from "../components/Modal";
import { PageError } from "../components/PageError";
import { ProgressBar } from "../components/ProgressBar";
import { SegmentRow } from "../components/SegmentRow";
import { StatusBadge } from "../components/StatusBadge";
import { useSettings } from "../store/settings";
import { useToast } from "../store/toast";

const PAGE_SIZE = 80;

function updateSegmentData(
  previous: InfiniteData<SegmentPage> | undefined,
  segmentId: number,
  update: Partial<Segment>,
): InfiniteData<SegmentPage> | undefined {
  if (!previous) return previous;
  return {
    ...previous,
    pages: previous.pages.map((page) => ({
      ...page,
      items: page.items.map((segment) =>
        segment.id === segmentId ? { ...segment, ...update } : segment,
      ),
    })),
  };
}

function setSegmentInAllCaches(
  queryClient: ReturnType<typeof useQueryClient>,
  projectId: number,
  segmentId: number,
  update: Partial<Segment>,
) {
  queryClient.setQueriesData<InfiniteData<SegmentPage>>(
    { queryKey: queryKeys.allSegments(projectId) },
    (previous) => updateSegmentData(previous, segmentId, update),
  );
}

function formatCount(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function WorkbenchPage() {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { settings } = useSettings();
  const { notify } = useToast();
  const projectId = Number(params.projectId);
  const listRef = useRef<HTMLDivElement>(null);
  const [chapterId, setChapterId] = useState<number | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [startOpen, setStartOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [forceStream, setForceStream] = useState(false);
  const [liveProgress, setLiveProgress] = useState<{ done: number; total: number } | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [activeSegmentId, setActiveSegmentId] = useState<number | null>(null);
  const [singleStreamSegmentId, setSingleStreamSegmentId] = useState<number | null>(null);
  const [locateTarget, setLocateTarget] = useState<number | null>(null);
  const [batchConfirm, setBatchConfirm] = useState<"review" | "retranslate" | null>(null);

  const projectQuery = useQuery({
    queryKey: queryKeys.project(projectId),
    queryFn: () => api.project(projectId),
    enabled: Number.isFinite(projectId),
    refetchInterval: (query) => {
      const project = query.state.data;
      return project?.status === "parsing" || project?.status === "translating" ? 4000 : false;
    },
  });

  const project = projectQuery.data;
  const segmentsQuery = useInfiniteQuery({
    queryKey: queryKeys.segments(projectId, chapterId, status),
    queryFn: ({ pageParam }) =>
      api.segments(projectId, {
        page: pageParam,
        pageSize: PAGE_SIZE,
        chapterId,
        status,
      }),
    initialPageParam: 1,
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.page + 1 : undefined),
    enabled: Number.isFinite(projectId) && Boolean(project),
  });

  const segments = useMemo(() => {
    const seen = new Set<number>();
    return (segmentsQuery.data?.pages.flatMap((page) => page.items) ?? []).filter((segment) => {
      if (seen.has(segment.id)) return false;
      seen.add(segment.id);
      return true;
    });
  }, [segmentsQuery.data]);
  const segmentTotal = segmentsQuery.data?.pages[0]?.total ?? 0;

  const rowVirtualizer = useVirtualizer({
    count: segments.length + (segmentsQuery.hasNextPage ? 1 : 0),
    getScrollElement: () => listRef.current,
    estimateSize: () => 290,
    overscan: 5,
    getItemKey: (index) => segments[index]?.id ?? `loader-${index}`,
  });
  const virtualItems = rowVirtualizer.getVirtualItems();

  useEffect(() => {
    const last = virtualItems[virtualItems.length - 1];
    if (
      last &&
      last.index >= segments.length - 6 &&
      segmentsQuery.hasNextPage &&
      !segmentsQuery.isFetchingNextPage
    ) {
      void segmentsQuery.fetchNextPage();
    }
  }, [segments.length, segmentsQuery, virtualItems]);

  useEffect(() => {
    setSelectedIds(new Set());
    setActiveSegmentId(null);
  }, [chapterId, status]);

  const onStreamEvent = useCallback(
    (event: StreamEvent) => {
      if (event.type === "progress") {
        const done = Number(
          event.completed ?? Number(event.done ?? 0) + Number(event.reviewed ?? 0),
        );
        const total = Number(event.total ?? 0);
        if (total > 0) setLiveProgress({ done, total });
        if (event.running === false || event.project_status === "done") {
          setForceStream(false);
          setLiveProgress(null);
          if (event.running === false) setSingleStreamSegmentId(null);
        }
      }
      if (
        (event.type === "segment_done" || event.type === "segment_delta") &&
        activeSegmentId === null
      ) {
        const id = Number(event.segment_id);
        if (Number.isFinite(id)) setActiveSegmentId(id);
      }
      if (
        event.type === "segment_done" &&
        event.segment_id !== undefined &&
        Number(event.segment_id) === singleStreamSegmentId
      ) {
        setSingleStreamSegmentId(null);
        setForceStream(false);
        setLiveProgress(null);
      }
      if (event.type === "error" && event.message) {
        notify(String(event.message), "error");
      }
      if (
        event.type === "error" &&
        event.segment_id === undefined &&
        event.stage !== "chapter_summary"
      ) {
        setForceStream(false);
        setLiveProgress(null);
      }
      if (
        event.type === "error" &&
        event.segment_id !== undefined &&
        Number(event.segment_id) === singleStreamSegmentId
      ) {
        setSingleStreamSegmentId(null);
        setForceStream(false);
        setLiveProgress(null);
      }
      if (event.type === "completed" || event.type === "stopped") {
        setForceStream(false);
        setLiveProgress(null);
        void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      }
      if (event.type === "project_deleted") {
        setForceStream(false);
        setLiveProgress(null);
        queryClient.removeQueries({ queryKey: queryKeys.project(projectId) });
        queryClient.removeQueries({ queryKey: queryKeys.allSegments(projectId) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
        notify("这本书已在另一窗口中删除。", "info");
        navigate("/");
      }
    },
    [activeSegmentId, navigate, notify, projectId, queryClient, singleStreamSegmentId],
  );

  const streamEnabled =
    Boolean(project) &&
    (forceStream || project?.status === "translating" || project?.status === "parsing");
  const streamConnection = useProjectStream(projectId, streamEnabled, onStreamEvent);

  const startMutation = useMutation({
    mutationFn: (scope: TranslateScope | undefined) => api.startTranslation(projectId, scope),
    onSuccess: (result, scope) => {
      setStartOpen(false);
      setLiveProgress(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      const scoped = scope?.chapter_id !== undefined || scope?.segment_ids !== undefined;
      if (result.running) {
        setForceStream(true);
        if (scope?.segment_ids !== undefined) setSelectedIds(new Set());
        notify(
          scope?.chapter_id !== undefined
            ? "已开始翻译本章待处理段落。"
            : scope?.segment_ids !== undefined
              ? "已开始翻译所选待处理段落。"
              : "翻译任务已启动。",
          "success",
        );
      } else {
        setForceStream(false);
        void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
        notify(scoped ? "所选范围内没有待翻译的段落。" : "没有待翻译或可重试的段落。", "info");
      }
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const stopMutation = useMutation({
    mutationFn: () => api.stopTranslation(projectId),
    onSuccess: () => {
      setForceStream(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      notify("已发送停止请求，正在保存当前进度。", "info");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const updateMutation = useMutation({
    mutationFn: ({
      id,
      patch,
    }: {
      id: number;
      patch: { target_text?: string; status?: string; reviewed?: boolean };
    }) => api.updateSegment(id, patch),
    onMutate: async ({ id, patch }) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.allSegments(projectId) });
      const previous = queryClient.getQueriesData<InfiniteData<SegmentPage>>({
        queryKey: queryKeys.allSegments(projectId),
      });
      const optimistic: Partial<Segment> = {
        ...patch,
        status:
          patch.status ??
          (patch.reviewed === true
            ? "reviewed"
            : patch.reviewed === false
              ? "done"
              : patch.target_text !== undefined
                ? patch.target_text.trim()
                  ? "done"
                  : "pending"
                : undefined),
      };
      setSegmentInAllCaches(queryClient, projectId, id, optimistic);
      return { previous };
    },
    onSuccess: (result) => {
      setSegmentInAllCaches(queryClient, projectId, result.id, result);
      void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.qa(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.tmStats(projectId) });
    },
    onError: (error, _variables, context) => {
      context?.previous.forEach(([key, value]: [QueryKey, InfiniteData<SegmentPage> | undefined]) => {
        queryClient.setQueryData(key, value);
      });
      notify(errorMessage(error), "error");
    },
  });

  const retranslateMutation = useMutation({
    mutationFn: (id: number) => api.retranslateSegment(id),
    onMutate: (id) => {
      setSegmentInAllCaches(queryClient, projectId, id, {
        target_text: "",
        status: "processing",
        error_msg: null,
      });
    },
    onSuccess: (_result, id) => {
      setForceStream(true);
      setSingleStreamSegmentId(id);
      setSegmentInAllCaches(queryClient, projectId, id, {
        target_text: "",
        status: "processing",
        error_msg: null,
      });
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      notify("已提交单段重译。", "success");
    },
    onError: (error) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      notify(errorMessage(error), "error");
    },
  });

  const configMutation = useMutation({
    mutationFn: (provider_cfg: ProviderConfig) =>
      api.updateProject(projectId, { provider_cfg }),
    onSuccess: () => {
      setConfigOpen(false);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      notify("当前书目的模型设置已保存。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const batchMutation = useMutation({
    mutationFn: (action: "review" | "retranslate") =>
      api.batchSegments(projectId, {
        segment_ids: [...selectedIds],
        action: action === "review" ? "mark_reviewed" : "retranslate",
        start_translation: action === "retranslate",
      }),
    onMutate: (action) => {
      if (action !== "retranslate") return;
      selectedIds.forEach((id) => {
        setSegmentInAllCaches(queryClient, projectId, id, {
          target_text: "",
          status: "processing",
          error_msg: null,
        });
      });
    },
    onSuccess: (_result, action) => {
      setBatchConfirm(null);
      if (action === "retranslate") {
        setForceStream(true);
        if (selectedIds.size === 1) setSingleStreamSegmentId([...selectedIds][0]);
      } else {
        void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      }
      setSelectedIds(new Set());
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      notify(action === "review" ? "所选段落已标记为校对完成。" : "所选段落已提交重译。", "success");
    },
    onError: (error, action) => {
      if (action === "retranslate") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      }
      notify(errorMessage(error), "error");
    },
  });

  const locateSegment = useCallback((id: number) => {
    setChapterId(undefined);
    setStatus(undefined);
    setSidebarOpen(true);
    setLocateTarget(id);
  }, []);

  useEffect(() => {
    if (locateTarget === null) return;
    const index = segments.findIndex((segment) => segment.id === locateTarget);
    if (index >= 0) {
      rowVirtualizer.scrollToIndex(index, { align: "center" });
      setActiveSegmentId(locateTarget);
      window.setTimeout(() => document.getElementById(`segment-${locateTarget}`)?.focus(), 80);
      setLocateTarget(null);
    } else if (segmentsQuery.hasNextPage && !segmentsQuery.isFetchingNextPage) {
      void segmentsQuery.fetchNextPage();
    } else if (!segmentsQuery.isLoading && !segmentsQuery.isFetchingNextPage) {
      notify("没有在当前书目中找到对应段落。", "error");
      setLocateTarget(null);
    }
  }, [locateTarget, notify, rowVirtualizer, segments, segmentsQuery]);

  const scrollToIndex = useCallback(
    (index: number) => {
      if (index < 0 || index >= segments.length) return;
      rowVirtualizer.scrollToIndex(index, { align: "center" });
      const segment = segments[index];
      setActiveSegmentId(segment.id);
      window.setTimeout(() => document.getElementById(`segment-${segment.id}`)?.focus(), 60);
    },
    [rowVirtualizer, segments],
  );

  useEffect(() => {
    if (!settings.shortcutsEnabled) return;
    const handleKey = (event: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      const element = event.target as HTMLElement;
      const editable =
        element.tagName === "INPUT" ||
        element.tagName === "TEXTAREA" ||
        element.tagName === "SELECT" ||
        element.isContentEditable;
      if (editable || event.ctrlKey || event.metaKey || event.altKey) return;
      const index = segments.findIndex((segment) => segment.id === activeSegmentId);
      if (event.key.toLowerCase() === "j") {
        event.preventDefault();
        scrollToIndex(Math.min(segments.length - 1, Math.max(0, index + 1)));
      } else if (event.key.toLowerCase() === "k") {
        event.preventDefault();
        scrollToIndex(Math.max(0, index <= 0 ? 0 : index - 1));
      } else if (event.key.toLowerCase() === "r" && index >= 0) {
        event.preventDefault();
        const segment = segments[index];
        if (segment.target_text && segment.status !== "processing") {
          updateMutation.mutate({
            id: segment.id,
            patch: {
              status: segment.status === "reviewed" ? "done" : "reviewed",
              target_text: segment.target_text ?? "",
            },
          });
        }
      } else if (event.key.toLowerCase() === "t" && index >= 0) {
        event.preventDefault();
        if (segments[index].status !== "processing") {
          retranslateMutation.mutate(segments[index].id);
        }
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [
    activeSegmentId,
    retranslateMutation,
    scrollToIndex,
    segments,
    settings.shortcutsEnabled,
    updateMutation,
  ]);

  if (!Number.isFinite(projectId)) {
    return <PageError title="无效的书目标识" message="请从书库重新进入翻译工作台。" />;
  }
  if (projectQuery.isError) {
    return (
      <PageError
        title="无法打开这本书"
        message={errorMessage(projectQuery.error)}
        onRetry={() => void projectQuery.refetch()}
      />
    );
  }
  if (!project) {
    return (
      <div className="mx-auto max-w-[1920px] animate-pulse px-4 py-6 sm:px-6">
        <div className="h-10 w-1/3 rounded bg-ink-100 dark:bg-ink-800" />
        <div className="mt-5 h-[70vh] rounded-2xl bg-ink-100 dark:bg-ink-800" />
      </div>
    );
  }

  const baseProgress = projectProgress(project);
  const progress = liveProgress?.total
    ? {
        done: liveProgress.done,
        total: liveProgress.total,
        percentage: (liveProgress.done / liveProgress.total) * 100,
      }
    : baseProgress;
  const isTranslating = project.status === "translating" || forceStream;
  const selectableSegments = segments.filter(
    (segment) => segment.status !== "processing",
  );
  const allLoadedSelected =
    selectableSegments.length > 0 &&
    selectableSegments.every((segment) => selectedIds.has(segment.id));

  return (
    <div className="flex h-[calc(100vh-4rem)] min-h-[38rem] flex-col overflow-hidden">
      <header className="shrink-0 border-b hairline bg-paper/70 px-3 py-3 dark:bg-ink-950/55 sm:px-5">
        <div className="mx-auto flex max-w-[1920px] items-center gap-3">
          <button type="button" className="icon-btn" onClick={() => navigate("/")} aria-label="返回书库" title="返回书库">
            <ArrowLeft className="size-5" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h1 className="truncate font-serif text-lg font-semibold text-ink-950 dark:text-white sm:text-xl">
                {project.title}
              </h1>
              <StatusBadge status={isTranslating ? "translating" : project.status} />
              {streamEnabled ? (
                <span
                  className="hidden items-center gap-1.5 text-[10px] text-ink-400 sm:flex"
                  title={`实时连接：${streamConnection}`}
                >
                  <span
                    className={`size-1.5 rounded-full ${
                      streamConnection === "open"
                        ? "bg-emerald-500"
                        : streamConnection === "retrying"
                          ? "animate-soft-pulse bg-amber-500"
                          : "animate-soft-pulse bg-blue-500"
                    }`}
                  />
                  {streamConnection === "open" ? "实时同步" : streamConnection === "retrying" ? "正在重连" : "连接中"}
                </span>
              ) : null}
            </div>
            <p className="mt-0.5 truncate text-[11px] text-ink-400">
              {project.source_type.toUpperCase()} · {project.source_lang} → {project.target_lang} · {String(project.provider_cfg.model ?? "默认模型")}
            </p>
          </div>

          <div className="hidden w-48 xl:block">
            <ProgressBar value={progress.percentage} compact />
            <p className="mt-1 text-right font-mono text-[10px] tabular-nums text-ink-400">
              {formatCount(progress.done)} / {formatCount(progress.total)} 段
            </p>
          </div>

          <button
            type="button"
            className="icon-btn hidden sm:inline-flex"
            onClick={() => setConfigOpen(true)}
            aria-label="当前书目设置"
            title="当前书目设置"
          >
            <Settings2 className="size-4" />
          </button>
          <button type="button" className="btn-secondary hidden sm:inline-flex" onClick={() => setExportOpen(true)}>
            <Download className="size-4" />
            导出
          </button>
          {isTranslating ? (
            <button
              type="button"
              className="btn-danger"
              onClick={() => stopMutation.mutate()}
              disabled={stopMutation.isPending}
            >
              {stopMutation.isPending ? <Square className="size-3.5 animate-pulse fill-current" /> : <Pause className="size-4" />}
              <span className="hidden sm:inline">{stopMutation.isPending ? "停止中" : "停止"}</span>
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary"
              onClick={() => setStartOpen(true)}
              disabled={project.status === "parsing"}
            >
              <Play className="size-3.5 fill-current" />
              <span className="hidden sm:inline">开始翻译</span>
            </button>
          )}
        </div>
      </header>

      <div className="shrink-0 border-b hairline bg-white/65 px-3 py-2 dark:bg-ink-900/60 sm:px-5">
        <div className="mx-auto flex max-w-[1920px] flex-wrap items-center gap-2">
          <div className="relative min-w-0 flex-1 sm:max-w-64">
            <Layers3 className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-ink-400" />
            <select
              className="field min-h-9 appearance-none py-1.5 pl-8 pr-8 text-xs"
              value={chapterId ?? ""}
              onChange={(event) => setChapterId(event.target.value ? Number(event.target.value) : undefined)}
              aria-label="章节筛选"
            >
              <option value="">全部章节</option>
              {project.chapters.map((chapter) => (
                <option key={chapter.id} value={chapter.id}>
                  {chapter.ord + 1}. {chapter.title || `第 ${chapter.ord + 1} 章`}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-400" />
          </div>
          {chapterId !== undefined && !isTranslating ? (
            <button
              type="button"
              className="btn-secondary min-h-9 whitespace-nowrap px-2.5 py-1.5 text-xs"
              onClick={() => startMutation.mutate({ chapter_id: chapterId })}
              disabled={startMutation.isPending || project.status === "parsing"}
              title="仅翻译本章的待处理段落，不影响已有译文"
            >
              <Play className="size-3.5 fill-current" />
              <span className="hidden sm:inline">翻译本章</span>
            </button>
          ) : null}
          <div className="relative w-32 sm:w-36">
            <Filter className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-ink-400" />
            <select
              className="field min-h-9 appearance-none py-1.5 pl-8 pr-8 text-xs"
              value={status ?? ""}
              onChange={(event) => setStatus(event.target.value || undefined)}
              aria-label="状态筛选"
            >
              <option value="">全部状态</option>
              <option value="pending">待翻译</option>
              <option value="processing">翻译中</option>
              <option value="done">已翻译</option>
              <option value="reviewed">已校对</option>
              <option value="error">需处理</option>
            </select>
            <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-400" />
          </div>
          <label className="hidden cursor-pointer items-center gap-2 rounded-lg px-2 py-2 text-xs text-ink-500 hover:bg-ink-100 dark:hover:bg-ink-800 md:flex">
            <input
              type="checkbox"
              className="size-4 accent-cinnabar-700"
              checked={allLoadedSelected}
              onChange={(event) =>
                setSelectedIds(
                  event.target.checked
                    ? new Set(selectableSegments.map((segment) => segment.id))
                    : new Set(),
                )
              }
            />
            选择已加载
          </label>
          <span className="hidden text-[11px] tabular-nums text-ink-400 md:inline">
            {formatCount(segmentTotal)} 段
          </span>
          <button
            type="button"
            className="icon-btn ml-auto"
            onClick={() => void segmentsQuery.refetch()}
            disabled={segmentsQuery.isFetching}
            title="刷新段落"
            aria-label="刷新段落"
          >
            <RefreshCw className={`size-4 ${segmentsQuery.isFetching ? "animate-spin" : ""}`} />
          </button>
          <button
            type="button"
            className="icon-btn sm:hidden"
            onClick={() => setExportOpen(true)}
            aria-label="导出译本"
          >
            <Download className="size-4" />
          </button>
          <button
            type="button"
            className="btn-secondary min-h-9 px-2.5 py-1.5 text-xs"
            onClick={() => setSidebarOpen((value) => !value)}
            aria-expanded={sidebarOpen}
          >
            {sidebarOpen ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
            <span className="hidden sm:inline">术语与 QA</span>
          </button>
        </div>
      </div>

      <div
        className={`mx-auto grid min-h-0 w-full max-w-[1920px] flex-1 ${
          sidebarOpen ? "lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,1fr)_22rem]" : "grid-cols-1"
        }`}
      >
        <div className="relative min-h-0 min-w-0">
          <div
            ref={listRef}
            className="h-full overflow-y-auto overscroll-contain px-3 py-3 sm:px-5"
            aria-label="翻译分段列表"
          >
            {segmentsQuery.isLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, index) => (
                  <div key={index} className="surface h-64 animate-pulse rounded-xl">
                    <div className="h-11 border-b hairline bg-ink-50 dark:bg-ink-800" />
                  </div>
                ))}
              </div>
            ) : segmentsQuery.isError ? (
              <div className="surface rounded-xl">
                <EmptyState
                  compact
                  icon={RefreshCw}
                  title="段落暂时无法加载"
                  description={errorMessage(segmentsQuery.error)}
                  action={
                    <button type="button" className="btn-secondary" onClick={() => void segmentsQuery.refetch()}>
                      <RefreshCw className="size-4" />重新加载
                    </button>
                  }
                />
              </div>
            ) : segments.length === 0 ? (
              <div className="surface rounded-xl">
                <EmptyState
                  compact
                  icon={Filter}
                  title={chapterId || status ? "当前筛选下没有段落" : "还没有可编辑段落"}
                  description={
                    chapterId || status
                      ? "清除章节或状态筛选，查看其他内容。"
                      : project.status === "parsing"
                        ? "原文件仍在解析，请稍候。"
                        : "请确认书目已完成解析。"
                  }
                  action={
                    chapterId || status ? (
                      <button
                        type="button"
                        className="btn-secondary"
                        onClick={() => {
                          setChapterId(undefined);
                          setStatus(undefined);
                        }}
                      >
                        <X className="size-4" />清除筛选
                      </button>
                    ) : undefined
                  }
                />
              </div>
            ) : (
              <div
                className="relative w-full"
                style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
              >
                {virtualItems.map((virtualRow) => {
                  const segment = segments[virtualRow.index];
                  return (
                    <div
                      key={virtualRow.key}
                      ref={rowVirtualizer.measureElement}
                      data-index={virtualRow.index}
                      className="absolute left-0 top-0 w-full pb-3"
                      style={{ transform: `translateY(${virtualRow.start}px)` }}
                    >
                      {segment ? (
                        <SegmentRow
                          segment={segment}
                          selected={selectedIds.has(segment.id)}
                          active={activeSegmentId === segment.id}
                          saving={updateMutation.isPending && updateMutation.variables?.id === segment.id}
                          retranslating={
                            retranslateMutation.isPending &&
                            retranslateMutation.variables === segment.id
                          }
                          shortcutsEnabled={settings.shortcutsEnabled}
                          onSelect={(checked) =>
                            setSelectedIds((current) => {
                              const next = new Set(current);
                              if (checked) next.add(segment.id);
                              else next.delete(segment.id);
                              return next;
                            })
                          }
                          onActivate={() => setActiveSegmentId(segment.id)}
                          onSave={async (targetText) => {
                            await updateMutation.mutateAsync({
                              id: segment.id,
                              patch: { target_text: targetText },
                            });
                            notify("译文已保存。", "success");
                          }}
                          onReview={() =>
                            updateMutation.mutate({
                              id: segment.id,
                              patch: {
                                status: segment.status === "reviewed" ? "done" : "reviewed",
                                reviewed: segment.status !== "reviewed",
                                target_text: segment.target_text ?? "",
                              },
                            })
                          }
                          onRetranslate={() => retranslateMutation.mutate(segment.id)}
                        />
                      ) : (
                        <div className="flex h-20 items-center justify-center text-xs text-ink-400">
                          <RefreshCw className="mr-2 size-4 animate-spin" />
                          正在加载更多段落…
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {selectedIds.size > 0 ? (
            <div className="absolute bottom-4 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-ink-700 bg-ink-950 p-2 pl-3 text-white shadow-float dark:border-ink-600">
              <span className="whitespace-nowrap text-xs tabular-nums">已选 {selectedIds.size} 段</span>
              <span className="h-5 w-px bg-white/15" />
              <button
                type="button"
                className="inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2 text-xs hover:bg-white/10 disabled:opacity-40"
                onClick={() => startMutation.mutate({ segment_ids: [...selectedIds] })}
                disabled={isTranslating || startMutation.isPending}
                title="仅翻译所选中的待处理段落，保留已有译文"
              >
                <Play className="size-3.5 fill-current" />翻译所选
              </button>
              <button
                type="button"
                className="inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2 text-xs hover:bg-white/10"
                onClick={() => setBatchConfirm("review")}
              >
                <CheckCheck className="size-3.5" />校对完成
              </button>
              <button
                type="button"
                className="inline-flex min-h-8 items-center gap-1.5 rounded-lg px-2 text-xs hover:bg-white/10 disabled:opacity-40"
                onClick={() => setBatchConfirm("retranslate")}
                disabled={isTranslating || batchMutation.isPending}
                title="丢弃所选段落的现有译文并重新翻译"
              >
                <RotateCw className="size-3.5" />批量重译
              </button>
              <button
                type="button"
                className="grid size-8 place-items-center rounded-lg text-white/60 hover:bg-white/10 hover:text-white"
                onClick={() => setSelectedIds(new Set())}
                aria-label="清除选择"
              >
                <X className="size-4" />
              </button>
            </div>
          ) : null}
        </div>

        {sidebarOpen ? (
          <>
            <button
              type="button"
              className="fixed inset-0 top-16 z-40 bg-ink-950/45 backdrop-blur-[1px] lg:hidden"
              onClick={() => setSidebarOpen(false)}
              aria-label="关闭侧边栏"
            />
            <aside className="fixed bottom-0 right-0 top-16 z-50 w-[min(90vw,23rem)] border-l hairline shadow-float lg:static lg:z-auto lg:block lg:w-auto lg:shadow-none">
              <button
                type="button"
                className="icon-btn absolute right-2 top-2 z-10 bg-white/80 lg:hidden dark:bg-ink-900/80"
                onClick={() => setSidebarOpen(false)}
                aria-label="关闭侧边栏"
              >
                <X className="size-4" />
              </button>
              <WorkbenchSidebar projectId={projectId} onLocate={locateSegment} />
            </aside>
          </>
        ) : null}
      </div>

      <StartTranslationDialog
        open={startOpen}
        project={project}
        pending={startMutation.isPending}
        onClose={() => setStartOpen(false)}
        onConfirm={() => startMutation.mutate(undefined)}
      />
      <ExportDialog open={exportOpen} project={project} onClose={() => setExportOpen(false)} />
      <ProjectConfigDialog
        open={configOpen}
        project={project}
        pending={configMutation.isPending}
        onClose={() => setConfigOpen(false)}
        onSave={(config) => configMutation.mutate(config)}
      />

      <Modal
        open={batchConfirm !== null}
        onClose={() => !batchMutation.isPending && setBatchConfirm(null)}
        title={batchConfirm === "review" ? "批量标记校对完成？" : "批量重新翻译？"}
        description={
          batchConfirm === "review"
            ? "仅有译文的段落会被标记为已校对。"
            : "重译会调用当前模型并替换所选段落的现有译文。"
        }
        size="sm"
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => setBatchConfirm(null)} disabled={batchMutation.isPending}>
              取消
            </button>
            <button
              type="button"
              className={batchConfirm === "retranslate" ? "btn-danger" : "btn-primary"}
              onClick={() => batchConfirm && batchMutation.mutate(batchConfirm)}
              disabled={batchMutation.isPending}
            >
              {batchConfirm === "review" ? <CheckCheck className="size-4" /> : <RotateCw className="size-4" />}
              {batchMutation.isPending ? "处理中" : `确认处理 ${selectedIds.size} 段`}
            </button>
          </>
        }
      >
        <p className="text-sm leading-6 text-ink-600 dark:text-ink-300">
          已选择 <strong className="font-semibold text-ink-900 dark:text-white">{selectedIds.size}</strong> 个段落。
          {batchConfirm === "retranslate" ? "本操作可能产生额外模型费用。" : ""}
        </p>
      </Modal>
    </div>
  );
}
