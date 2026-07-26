import {
  ArrowRight,
  BookOpen,
  FileText,
  LibraryBig,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, errorMessage, projectProgress } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import type { Project } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { Modal } from "../components/Modal";
import { PageError } from "../components/PageError";
import { ProgressBar } from "../components/ProgressBar";
import { StatusBadge } from "../components/StatusBadge";
import { providerConfigFromSettings, useSettings } from "../store/settings";
import { useToast } from "../store/toast";

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "short",
  day: "numeric",
  year: "numeric",
});

function formatDate(date?: string): string {
  if (!date) return "刚刚创建";
  const parsed = new Date(date);
  return Number.isNaN(parsed.valueOf()) ? "最近更新" : dateFormatter.format(parsed);
}

function BookCover({ project }: { project: Project }) {
  const isEpub = project.source_type.toLowerCase() === "epub";
  return (
    <div
      className={`relative flex aspect-[3/4] w-full items-end overflow-hidden rounded-xl border p-4 shadow-inner ${
        isEpub
          ? "border-amber-900/20 bg-[#5b3527] text-[#fff8e9] dark:bg-[#6c4031]"
          : "border-slate-900/15 bg-[#d9e2e2] text-slate-800 dark:bg-[#536567] dark:text-slate-50"
      }`}
      aria-hidden="true"
    >
      <div className="absolute inset-y-0 left-3 w-px bg-white/20" />
      <div className="absolute right-3 top-3 opacity-35">
        {isEpub ? <BookOpen className="size-8" strokeWidth={1.2} /> : <FileText className="size-8" strokeWidth={1.2} />}
      </div>
      <div className="relative pl-2">
        <span className="block text-[10px] font-semibold uppercase tracking-[0.22em] opacity-65">
          {isEpub ? "EPUB" : "MARKDOWN"}
        </span>
        <span className="mt-1 line-clamp-3 block font-serif text-lg font-semibold leading-tight">
          {project.title}
        </span>
      </div>
    </div>
  );
}

function LibrarySkeleton() {
  return (
    <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="surface animate-pulse rounded-2xl p-4">
          <div className="aspect-[3/4] rounded-xl bg-ink-100 dark:bg-ink-800" />
          <div className="mt-4 h-5 w-2/3 rounded bg-ink-100 dark:bg-ink-800" />
          <div className="mt-3 h-2 rounded bg-ink-100 dark:bg-ink-800" />
        </div>
      ))}
    </div>
  );
}

function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { settings, providerSettingsResolved } = useSettings();
  const { notify } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [sourceLang, setSourceLang] = useState("en");
  const [targetLang, setTargetLang] = useState("zh-CN");
  const [dragActive, setDragActive] = useState(false);

  const close = () => {
    if (upload.isPending) return;
    setFile(null);
    setTitle("");
    setDragActive(false);
    onClose();
  };

  const acceptFile = (candidate?: File | null) => {
    if (!candidate) return;
    const extension = candidate.name.split(".").pop()?.toLowerCase();
    if (extension !== "epub" && extension !== "md" && extension !== "markdown") {
      notify("仅支持 EPUB、MD 或 Markdown 文件。", "error");
      return;
    }
    setFile(candidate);
    if (!title) setTitle(candidate.name.replace(/\.(epub|md|markdown)$/i, ""));
  };

  const upload = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("请先选择文件。");
      return api.upload({
        file,
        title,
        sourceLang,
        targetLang,
        providerCfg: providerSettingsResolved
          ? providerConfigFromSettings(settings)
          : undefined,
      });
    },
    onSuccess: (project) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      notify("书目已导入，正在准备翻译账本。", "success");
      close();
      navigate(`/projects/${project.id}`);
    },
    onError: (error) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      notify(errorMessage(error), "error");
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      notify("请选择一本 EPUB 或 Markdown 文件。", "error");
      return;
    }
    upload.mutate();
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    acceptFile(event.dataTransfer.files[0]);
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="导入一本书"
      description="文件仅保存在本机。导入后会解析章节并建立可断点续跑的翻译账本。"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={close} disabled={upload.isPending}>
            取消
          </button>
          <button
            type="submit"
            form="upload-project-form"
            className="btn-primary min-w-28"
            disabled={!file || upload.isPending}
          >
            {upload.isPending ? (
              <>
                <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                解析中
              </>
            ) : (
              <>
                <UploadCloud className="size-4" />
                导入书目
              </>
            )}
          </button>
        </>
      }
    >
      <form id="upload-project-form" onSubmit={submit} className="space-y-5">
        <div
          className={`group relative grid min-h-44 cursor-pointer place-items-center rounded-xl border-2 border-dashed p-5 text-center transition ${
            dragActive
              ? "border-cinnabar-500 bg-cinnabar-50 dark:bg-cinnabar-950/30"
              : file
                ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-800 dark:bg-emerald-950/25"
                : "border-ink-200 bg-ink-50/60 hover:border-ink-300 hover:bg-ink-50 dark:border-ink-700 dark:bg-ink-950/30 dark:hover:border-ink-600"
          }`}
          onClick={() => inputRef.current?.click()}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragActive(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragActive(false);
          }}
          onDrop={onDrop}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
          }}
          aria-label="选择或拖放书籍文件"
        >
          <input
            ref={inputRef}
            type="file"
            accept=".epub,.md,.markdown,application/epub+zip,text/markdown"
            className="sr-only"
            onChange={(event) => acceptFile(event.target.files?.[0])}
          />
          {file ? (
            <div>
              <div className="mx-auto grid size-11 place-items-center rounded-xl bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300">
                {file.name.toLowerCase().endsWith(".epub") ? (
                  <BookOpen className="size-5" />
                ) : (
                  <FileText className="size-5" />
                )}
              </div>
              <p className="mt-3 max-w-sm truncate text-sm font-medium text-ink-900 dark:text-white">{file.name}</p>
              <p className="mt-1 text-xs text-ink-500">{(file.size / 1024 / 1024).toFixed(2)} MB · 点击更换</p>
              <button
                type="button"
                className="icon-btn absolute right-2 top-2"
                onClick={(event) => {
                  event.stopPropagation();
                  setFile(null);
                }}
                aria-label="移除文件"
              >
                <X className="size-4" />
              </button>
            </div>
          ) : (
            <div>
              <div className="mx-auto grid size-11 place-items-center rounded-xl border border-ink-200 bg-white text-ink-500 shadow-sm transition group-hover:-translate-y-0.5 dark:border-ink-700 dark:bg-ink-900">
                <UploadCloud className="size-5" />
              </div>
              <p className="mt-3 text-sm font-medium text-ink-800 dark:text-ink-100">
                拖放文件到此处，或点击选择
              </p>
              <p className="mt-1 text-xs text-ink-500">支持 .epub、.md、.markdown</p>
            </div>
          )}
        </div>

        <div>
          <label className="field-label" htmlFor="project-title">书名</label>
          <input
            id="project-title"
            className="field"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="默认使用文件名"
          />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="field-label" htmlFor="source-language">原文语言</label>
            <select
              id="source-language"
              className="field"
              value={sourceLang}
              onChange={(event) => setSourceLang(event.target.value)}
            >
              <option value="en">英语</option>
              <option value="ja">日语</option>
              <option value="fr">法语</option>
              <option value="de">德语</option>
              <option value="es">西班牙语</option>
              <option value="auto">自动检测</option>
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="target-language">译文语言</label>
            <select
              id="target-language"
              className="field"
              value={targetLang}
              onChange={(event) => setTargetLang(event.target.value)}
            >
              <option value="zh-CN">简体中文</option>
              <option value="zh-TW">繁体中文</option>
              <option value="en">英语</option>
              <option value="ja">日语</option>
            </select>
          </div>
        </div>
        <p className="rounded-lg bg-ink-50 px-3 py-2.5 text-xs leading-5 text-ink-500 dark:bg-ink-800/60 dark:text-ink-400">
          将使用设置中的 <strong className="font-medium text-ink-700 dark:text-ink-200">{settings.model}</strong>
          ，温度 {settings.temperature}，并发 {settings.maxConcurrency}。
        </p>
      </form>
    </Modal>
  );
}

export function LibraryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { notify } = useToast();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<"updated" | "title" | "progress">("updated");

  const projectsQuery = useQuery({
    queryKey: queryKeys.projects,
    queryFn: api.projects,
    refetchInterval: (query) =>
      query.state.data?.some((project) => project.status === "parsing" || project.status === "translating")
        ? 3000
        : false,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteProject(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
      setDeleteTarget(null);
      notify("书目及其翻译数据已删除。", "success");
    },
    onError: (error) => notify(errorMessage(error), "error"),
  });

  const filteredProjects = useMemo(() => {
    const term = search.trim().toLocaleLowerCase();
    const result = (projectsQuery.data ?? []).filter((project) =>
      `${project.title} ${project.provider_cfg.model ?? ""}`.toLocaleLowerCase().includes(term),
    );
    return result.sort((a, b) => {
      if (sort === "title") return a.title.localeCompare(b.title, "zh-CN");
      if (sort === "progress") return projectProgress(b).percentage - projectProgress(a).percentage;
      return new Date(b.updated_at ?? b.created_at ?? 0).valueOf() - new Date(a.updated_at ?? a.created_at ?? 0).valueOf();
    });
  }, [projectsQuery.data, search, sort]);

  const metrics = useMemo(() => {
    const projects = projectsQuery.data ?? [];
    return {
      total: projects.length,
      active: projects.filter((project) => project.status === "translating" || project.status === "parsing").length,
      completed: projects.filter((project) => projectProgress(project).percentage >= 100 || project.status === "done").length,
    };
  }, [projectsQuery.data]);

  if (projectsQuery.isError) {
    return <PageError message={errorMessage(projectsQuery.error)} onRetry={() => void projectsQuery.refetch()} />;
  }

  return (
    <>
      <div className="mx-auto max-w-7xl px-4 py-8 sm:px-6 sm:py-12">
        <div className="flex flex-col gap-6 border-b hairline pb-8 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="eyebrow">Translation library</p>
            <h1 className="mt-2 font-serif text-3xl font-semibold tracking-tight text-ink-950 dark:text-white sm:text-4xl">
              我的书库
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-6 text-ink-500 dark:text-ink-400">
              导入原书，持续翻译，并在一个安静的工作台里完成逐段校订。
            </p>
          </div>
          <button type="button" className="btn-primary self-start md:self-auto" onClick={() => setUploadOpen(true)}>
            <Plus className="size-4" />
            导入书籍
          </button>
        </div>

        <div className="mt-6 grid grid-cols-3 gap-3 sm:max-w-lg">
          {[
            ["全部书目", metrics.total],
            ["正在处理", metrics.active],
            ["已经完成", metrics.completed],
          ].map(([label, value]) => (
            <div key={label} className="rounded-xl border hairline bg-white/55 px-3 py-3 dark:bg-ink-900/40">
              <span className="block font-serif text-xl font-semibold tabular-nums text-ink-950 dark:text-white">{value}</span>
              <span className="mt-0.5 block text-[11px] text-ink-500">{label}</span>
            </div>
          ))}
        </div>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative w-full sm:max-w-sm">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-400" />
            <input
              type="search"
              className="field pl-9"
              placeholder="搜索书名或模型"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="搜索书目"
            />
          </div>
          <label className="flex items-center gap-2 text-xs text-ink-500">
            排序
            <select
              className="field min-h-9 w-auto py-1.5"
              value={sort}
              onChange={(event) => setSort(event.target.value as typeof sort)}
              aria-label="书目排序"
            >
              <option value="updated">最近更新</option>
              <option value="title">书名</option>
              <option value="progress">完成进度</option>
            </select>
          </label>
        </div>

        <div className="mt-6">
          {projectsQuery.isLoading ? (
            <LibrarySkeleton />
          ) : (projectsQuery.data?.length ?? 0) === 0 ? (
            <div className="surface rounded-2xl">
              <EmptyState
                icon={LibraryBig}
                title="书架还是空的"
                description="导入 EPUB 或 Markdown 文件，系统会保留原始结构并建立逐段翻译账本。"
                action={
                  <button type="button" className="btn-primary" onClick={() => setUploadOpen(true)}>
                    <UploadCloud className="size-4" />
                    导入第一本书
                  </button>
                }
              />
            </div>
          ) : filteredProjects.length === 0 ? (
            <EmptyState
              icon={Search}
              title="没有找到匹配的书目"
              description="试试更短的关键词，或清除当前搜索条件。"
              action={
                <button type="button" className="btn-secondary" onClick={() => setSearch("")}>
                  清除搜索
                </button>
              }
            />
          ) : (
            <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {filteredProjects.map((project) => {
                const progress = projectProgress(project);
                return (
                  <article
                    key={project.id}
                    className="surface group cursor-pointer rounded-2xl p-4 transition duration-200 hover:-translate-y-0.5 hover:border-ink-300 hover:shadow-float dark:hover:border-ink-700"
                    onClick={() => navigate(`/projects/${project.id}`)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") navigate(`/projects/${project.id}`);
                    }}
                    tabIndex={0}
                    aria-label={`打开《${project.title}》翻译工作台`}
                  >
                    <BookCover project={project} />
                    <div className="mt-4 flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <h2 className="line-clamp-2 font-serif text-lg font-semibold leading-snug text-ink-950 dark:text-white">
                          {project.title}
                        </h2>
                        <p className="mt-1 truncate text-xs text-ink-500">
                          {String(project.provider_cfg.model ?? "尚未选择模型")}
                        </p>
                      </div>
                      <button
                        type="button"
                        className="icon-btn -mr-1 -mt-1 opacity-70 sm:opacity-0 sm:group-hover:opacity-100 sm:focus:opacity-100"
                        aria-label={`删除《${project.title}》`}
                        title="删除书目"
                        onClick={(event) => {
                          event.stopPropagation();
                          setDeleteTarget(project);
                        }}
                      >
                        <MoreHorizontal className="size-5" />
                      </button>
                    </div>
                    <div className="mt-4">
                      <ProgressBar value={progress.percentage} compact />
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <StatusBadge status={project.status} />
                        <span className="text-[11px] tabular-nums text-ink-500">
                          {progress.total > 0 ? `${progress.done} / ${progress.total} 段` : formatDate(project.updated_at)}
                        </span>
                      </div>
                    </div>
                    <div className="mt-4 flex items-center justify-between border-t hairline pt-3 text-xs text-ink-500">
                      <span>{project.source_type.toUpperCase()} · {project.source_lang} → {project.target_lang}</span>
                      <ArrowRight className="size-4 transition group-hover:translate-x-0.5 group-hover:text-cinnabar-600" />
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <UploadDialog open={uploadOpen} onClose={() => setUploadOpen(false)} />

      <Modal
        open={Boolean(deleteTarget)}
        onClose={() => !deleteMutation.isPending && setDeleteTarget(null)}
        title="删除这本书？"
        description="此操作会同时删除章节、译文、术语和本地上传文件，无法撤销。"
        size="sm"
        footer={
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setDeleteTarget(null)}
              disabled={deleteMutation.isPending}
            >
              取消
            </button>
            <button
              type="button"
              className="btn-danger"
              disabled={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              <Trash2 className="size-4" />
              {deleteMutation.isPending ? "正在删除" : "确认删除"}
            </button>
          </>
        }
      >
        <div className="rounded-xl border border-cinnabar-100 bg-cinnabar-50/60 p-4 dark:border-cinnabar-900 dark:bg-cinnabar-950/25">
          <p className="font-serif font-semibold text-ink-900 dark:text-white">{deleteTarget?.title}</p>
          <p className="mt-1 text-sm text-ink-500">
            建议先导出已有译文，再执行删除。
          </p>
        </div>
      </Modal>
    </>
  );
}
