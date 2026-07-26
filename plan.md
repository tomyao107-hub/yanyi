# AI 翻译工作台 — 建设计划（细化版）

> 用途：翻译英文书籍（支持 **epub** 与 **md** 输入），本地 Web 应用形态。
> 主要场景：个人阅读为主 + 可逐句精修；多 provider 可切换。
> 创建日期：2026-07-26 ｜ 细化日期：2026-07-26
>
> 本版在原计划基础上补齐：完整目录结构、SQLite DDL、Provider 抽象接口、
> 分段与稳定 ID 策略、Prompt/上下文窗口、并发重试、账本状态机、回写细节、
> QA 规则、REST+SSE API 清单、配置与密钥管理、测试策略、成本预估，
> 以及每个 Phase 的任务清单与验收标准。

---

## 1. 技术选型

### 后端
| 组件 | 选择 | 版本下限 | 理由 |
|---|---|---|---|
| 语言运行时 | Python | 3.11 | `asyncio.TaskGroup`、更快启动 |
| Web 框架 | FastAPI + Uvicorn | 0.110 / 0.29 | async、SSE、自动 OpenAPI |
| ORM | SQLModel（SQLAlchemy 2 + Pydantic） | 0.0.16 | 模型即 schema，省样板 |
| 迁移 | Alembic | 1.13 | schema 演进可回滚 |
| 模型接入 | **LiteLLM** | 1.40 | 一套接口切换所有 provider |
| epub | ebooklib + lxml + BeautifulSoup4 | — | 只译文本节点、保留标签 |
| md | markdown-it-py | 3.0 | token 流可无损回写 |
| 分词计数 | tiktoken（可选） | — | 估算 token/成本，缺失时用字符近似 |
| 校验 | Pydantic v2 | 2.7 | 配置与 API schema |
| 测试 | pytest + pytest-asyncio + httpx | — | 单元 + API |
| 打包运行 | uv 或 pip + venv | — | 依赖锁定 |

### 前端
| 组件 | 选择 | 理由 |
|---|---|---|
| 框架 | React 18 + Vite 5 + TypeScript | 快、生态成熟 |
| 样式 | Tailwind CSS 3 | 快速搭对照编辑器 |
| 长列表 | TanStack Virtual | 数千段虚拟化 |
| 数据层 | TanStack Query | 缓存、失效、乐观更新 |
| 流式 | 原生 EventSource（SSE） | 接收翻译进度/增量 |
| 路由 | React Router 6 | 书目/工作台分页 |

---

## 2. 仓库目录结构

```
trans/
├─ plan.md
├─ README.md
├─ .env.example              # provider key 占位，实际写 .env（gitignore）
├─ pyproject.toml            # 后端依赖 + 工具配置
├─ backend/
│  ├─ app/
│  │  ├─ main.py             # FastAPI 入口、CORS、路由挂载
│  │  ├─ config.py           # 读 .env / settings.toml，Settings 单例
│  │  ├─ db.py               # engine、session、init
│  │  ├─ models.py           # SQLModel 表（见 §3）
│  │  ├─ schemas.py          # API 出入参 DTO
│  │  ├─ parsers/
│  │  │  ├─ base.py          # DocModel 定义、Parser 协议
│  │  │  ├─ epub_parser.py   # ebooklib → DocModel
│  │  │  └─ md_parser.py     # markdown-it → DocModel
│  │  ├─ segment.py          # DocModel → Segment[]，稳定 ID
│  │  ├─ providers/
│  │  │  ├─ base.py          # TranslationProvider 协议
│  │  │  └─ litellm_provider.py
│  │  ├─ engine/
│  │  │  ├─ translator.py    # 编排：取 pending、注入上下文、并发、重试、落库
│  │  │  ├─ context.py       # 滚动上下文 + 术语注入 + 章节摘要
│  │  │  ├─ prompt.py        # 系统/用户 prompt 模板
│  │  │  └─ tm.py            # 翻译记忆匹配/写入
│  │  ├─ writers/
│  │  │  ├─ epub_writer.py   # 双语/纯译 epub 回写
│  │  │  └─ md_writer.py     # AST 回写
│  │  ├─ qa.py               # 漏译/结构/长度/术语检查
│  │  └─ api/
│  │     ├─ projects.py      # 书目 CRUD、上传、导出
│  │     ├─ segments.py      # 分段列表、单段编辑/重译/标记
│  │     ├─ glossary.py      # 术语表 CRUD
│  │     └─ stream.py        # SSE：翻译进度与增量
│  ├─ cli.py                 # Phase 1 命令行入口（不依赖前端即可跑通）
│  ├─ alembic/               # 迁移脚本
│  └─ tests/
│     ├─ fixtures/           # 小 epub/md 样本
│     ├─ test_parsers.py
│     ├─ test_segment.py
│     ├─ test_engine.py
│     ├─ test_writers.py
│     └─ test_api.py
├─ frontend/
│  ├─ index.html
│  ├─ vite.config.ts
│  ├─ package.json
│  └─ src/
│     ├─ main.tsx / App.tsx / router.tsx
│     ├─ api/                # fetch 封装、SSE hook、类型
│     ├─ pages/              # Library / Workbench / Settings
│     ├─ components/         # SegmentRow、DiffPane、GlossaryPanel、ProgressBar
│     └─ store/              # TanStack Query keys、本地 UI state
├─ data/
│  ├─ trans.db               # SQLite（gitignore）
│  └─ uploads/               # 上传原文件（gitignore）
└─ exports/                  # 导出产物（gitignore）
```

**运行时数据分离**：源文件、SQLite、导出物都放 `data/` 与 `exports/`，全部 gitignore，方便备份与清库重来。

---

## 3. 数据模型（SQLite DDL）

四张核心表 + 一张章节表（便于按章翻译/摘要/进度统计）。

```sql
-- 书 / 工程
CREATE TABLE project (
  id            INTEGER PRIMARY KEY,
  title         TEXT NOT NULL,
  source_lang   TEXT NOT NULL DEFAULT 'en',
  target_lang   TEXT NOT NULL DEFAULT 'zh-CN',
  source_type   TEXT NOT NULL,              -- 'epub' | 'md'
  source_path   TEXT NOT NULL,              -- data/uploads/xxx
  provider_cfg  TEXT NOT NULL DEFAULT '{}', -- JSON: {model, temperature, ...}
  status        TEXT NOT NULL DEFAULT 'created', -- created|parsing|ready|translating|done
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- 章节（epub=每个 spine 文档；md=顶层 H1/H2 或整篇）
CREATE TABLE chapter (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  ord           INTEGER NOT NULL,           -- 章内顺序
  title         TEXT,
  href          TEXT,                       -- epub 内部路径 / md 锚点
  summary       TEXT,                       -- 章节摘要，供后续段落做上下文
  UNIQUE(project_id, ord)
);

-- 段（翻译与账本的最小单位）
CREATE TABLE segment (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  chapter_id    INTEGER NOT NULL REFERENCES chapter(id) ON DELETE CASCADE,
  ord           INTEGER NOT NULL,           -- 全书顺序，用于取上下文与排序
  stable_key    TEXT NOT NULL,             -- 见 §5 稳定 ID，回写定位用
  struct_path   TEXT NOT NULL,             -- JSON：标签/块路径，回写用
  source_text   TEXT NOT NULL,
  target_text   TEXT,
  src_hash      TEXT NOT NULL,             -- normalize 后 sha1，TM 命中/断点判定
  status        TEXT NOT NULL DEFAULT 'pending',
              -- pending|processing|done|error|reviewed
  error_msg     TEXT,
  token_in      INTEGER,
  token_out     INTEGER,
  provider      TEXT,                       -- 实际使用的 model
  updated_at    TEXT NOT NULL,
  UNIQUE(project_id, stable_key)
);
CREATE INDEX ix_segment_proj_status ON segment(project_id, status);
CREATE INDEX ix_segment_proj_ord    ON segment(project_id, ord);

-- 术语表（人名/地名/专有名词，长篇一致性关键）
CREATE TABLE glossary_term (
  id            INTEGER PRIMARY KEY,
  project_id    INTEGER NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  source_term   TEXT NOT NULL,
  target_term   TEXT NOT NULL,
  note          TEXT,
  case_sensitive INTEGER NOT NULL DEFAULT 0,
  enabled       INTEGER NOT NULL DEFAULT 1,
  UNIQUE(project_id, source_term)
);

-- 翻译记忆（跨段/跨书复用，省钱省时）
CREATE TABLE tm_entry (
  id            INTEGER PRIMARY KEY,
  src_hash      TEXT NOT NULL,             -- normalize 后 sha1
  source_lang   TEXT NOT NULL,
  target_lang   TEXT NOT NULL,
  source_text   TEXT NOT NULL,
  target_text   TEXT NOT NULL,
  hit_count     INTEGER NOT NULL DEFAULT 0,
  updated_at    TEXT NOT NULL,
  UNIQUE(src_hash, source_lang, target_lang)
);
```

**要点**
- `src_hash` = sha1(归一化原文)：TM 命中、断点续跑「原文没变才复用译文」的判据。
- `stable_key` 全书唯一且与内容位置绑定（§5），重解析同一版本文件仍能对齐，回写靠它定位。
- `token_in/out` 落库，直接支撑成本统计与预估。
- 全部时间戳存 ISO8601 文本，跨平台无时区坑。

---

## 4. 文档模型与解析

统一「文档模型」`DocModel`，让 epub / md 汇入同一条流水线。

```python
@dataclass
class Block:
    kind: str          # 'para' | 'heading' | 'list_item' | 'blockquote' | 'code' | 'raw'
    text: str          # 纯文本（供翻译）；code/raw 为 None 表示跳过翻译
    struct_path: dict  # 回写定位：epub={file, xpath} / md={token_index}
    translatable: bool # code/公式/纯符号 → False

@dataclass
class Chapter:
    ord: int
    title: str | None
    href: str | None
    blocks: list[Block]

@dataclass
class DocModel:
    source_type: str
    chapters: list[Chapter]
```

**epub 解析**（`epub_parser.py`）
- ebooklib 遍历 `spine` → 每个 XHTML 文档一个 `Chapter`。
- lxml 解析 DOM，遍历块级元素（p / h1-6 / li / blockquote / pre）。
- 只取**文本节点**，`struct_path` 记 `{file: 'OEBPS/ch1.xhtml', xpath: '/html/body/div/p[3]'}`。
- `<pre>/<code>` 标 `translatable=False`；`<img>/<a>` 的属性不动，只译可见文字。
- 保留原始 DOM 引用/副本，回写时按 xpath 定位替换文本节点（§8）。

**md 解析**（`md_parser.py`）
- markdown-it-py 产出 token 流，`struct_path` 记 `{token_index: n}`。
- `inline` token 的 children 里，`code_inline` / `fence` / `html_block` 跳过。
- 链接文本译、URL 不动；表格单元格逐格译。
- 章节切分：默认按顶层 `# / ##` 切 `Chapter`，无标题则整篇一章。

**归一化 normalize()**（供 hash 与去重）
- 去首尾空白、合并连续空白为单空格、统一引号/破折号、NFC。
- 仅用于算 `src_hash`；`source_text` 存原样，回写不失真。

---

## 5. 分段与稳定 ID

**分段粒度**：以**块**（段落/标题/列表项/引用）为单位。
- 句子级会丢上下文、章节级难对照；块级是可读性与一致性的平衡点。
- 超长块（> ~1500 字符）可选按句号/分号软切为子段，`stable_key` 加 `#0/#1` 后缀，回写时按序拼回。

**stable_key 生成**
```
stable_key = f"{chapter.ord:04d}:{block_index:05d}:{sha1(struct_path)[:8]}"
```
- 前缀保证全书有序、可读、可排序。
- 末尾 struct_path 短 hash 兜底同章内块结构，重解析同版本文件仍稳定。
- 目的：**断点续跑与回写全靠它**——文件没变，key 就不变，只补 `pending/error` 段。

**导入即建账本**：解析后一次性批量 INSERT 所有 segment（status=pending），
之后翻译只是把 pending 推进到 done。这是「随时中断续跑」的地基。

---

## 6. Provider 抽象

```python
class TranslationProvider(Protocol):
    async def translate(
        self,
        text: str,
        *,
        system_prompt: str,
        context: str,          # 已拼好的上下文块（前文/术语/摘要）
        model: str,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> TranslationResult: ...

@dataclass
class TranslationResult:
    text: str
    token_in: int
    token_out: int
    model: str
    raw: dict | None = None
```

**LiteLLM 实现**（`litellm_provider.py`）
- 统一走 `litellm.acompletion(model=..., messages=[...])`。
- 模型串按 LiteLLM 约定：`gpt-4o` / `claude-3-5-sonnet` / `gemini/gemini-1.5-pro` /
  `deepseek/deepseek-chat` / `openrouter/...` / `ollama/qwen2.5`。
- key 从环境变量读（`OPENAI_API_KEY` 等），LiteLLM 自动识别，不在代码里硬编码。
- `token_in/out` 从返回 `usage` 取；缺失时 tiktoken/字符估算。
- `stream=True` 时逐 chunk yield，供 SSE 增量回前端。

**切换方式**：`project.provider_cfg` 存 `{model, temperature, max_concurrency}`，
运行时读取，无需改代码即可换 provider。

---

## 7. 翻译引擎

### 7.1 编排（translator.py）
```
loop:
  批量取 project 内 status in (pending,error) 的 segment（按 ord）
  for each（受并发信号量约束）:
    1. TM 命中？(src_hash) → 直接写 target，status=done，跳过调用
    2. 组装 context（§7.3）
    3. status=processing 落库
    4. provider.translate(...)（失败退避重试）
    5. 成功 → 写 target/token/provider，status=done；写入 TM
       失败 → status=error，记 error_msg
  直到无 pending
```

### 7.2 并发 / 重试 / 限流
- `asyncio.Semaphore(max_concurrency)`，默认 4，provider_cfg 可调。
- 重试：指数退避 `1,2,4,8s` + 抖动，最多 5 次；仅对 429/5xx/超时重试。
- 429 命中时全局降速（临时缩小信号量）。
- 单段超时 60s；整体可随时 Ctrl-C / 关请求，已 done 的不重跑。

### 7.3 上下文窗口（context.py）
拼给模型的 context 由三部分组成，控制在预算 token 内（默认 ≤1200）：
1. **术语表**：命中当前原文的术语，`原文 → 译文` 列表（超量则取本段出现的）。
2. **滚动上下文**：前 N 段（默认 2-3）的原文+已定译文，保证代词/语气衔接。
3. **章节摘要**：`chapter.summary`（首次翻译该章时生成一次并缓存）。

### 7.4 Prompt（prompt.py）
- **System**：定位「专业文学/技术译者，译成简体中文，忠实流畅、保留原意与语气；
  严格遵循术语表；只输出译文，不加解释；保留原文中的占位符/标记」。
- **User**：`{context}` + 明确分隔 + `【待译原文】{text}`。
- 约束输出纯译文（便于回写），必要时用轻量分隔符包裹防串味。

### 7.5 翻译记忆（tm.py）
- 译前查 `tm_entry(src_hash, langs)`；命中即复用并 `hit_count+1`。
- 译后写入/更新 TM。跨书共享，重复段（版权页、常见句）零成本。

---

## 8. 回写导出

### 8.1 账本状态机
```
pending ──取任务──▶ processing ──成功──▶ done ──人工校对──▶ reviewed
   ▲                     │
   └──────失败───────────┴──▶ error ──重译──▶ processing
```
- 只有 `done/reviewed` 参与导出；`pending/error` 导出时保留原文或留空（可配）。
- 「重译单段」= 强制置 processing 再跑，无视 TM。

### 8.2 epub 回写（epub_writer.py）
- 载入原 epub 的 DOM 副本，按 segment 的 `struct_path.xpath` 定位文本节点。
- **双语交错（默认）**：原文节点后插入译文节点（可加 class 供 CSS 区分/隐藏），
  阅读器里原文+译文成对出现，个人阅读体感最佳。
- **纯译文**：直接替换文本节点内容。
- 保留原 CSS/图片/目录/元数据；注入一小段样式控制双语间距与灰度。
- 用 ebooklib 重新打包为新 `.epub` 到 `exports/`。

### 8.3 md 回写（md_writer.py）
- 按 `struct_path.token_index` 把译文写回对应 token，markdown-it renderer 输出。
- 双语模式：译文块紧随原文块（用 `> ` 或空行分隔，可配）。
- code fence / 链接 URL / 表格结构原样保留。

### 8.4 导出选项
`{mode: 'bilingual'|'target_only', include_untranslated: bool, format: 'epub'|'md'}`

---

## 9. QA 检查（qa.py）

翻译后/导出前跑，产出问题列表（不阻断，供审校定位）：
| 检查 | 规则 | 级别 |
|---|---|---|
| 漏译 | status≠done/reviewed 或 target 为空 | error |
| 长度异常 | 中英字符比落在合理区间外（默认 0.3–3.0） | warn |
| 结构破损(md) | 回写后 token 数/类型与原文不一致 | error |
| 标签破损(epub) | 回写后该文档仍能被 lxml 解析 | error |
| 术语遵循 | 原文含术语但译文未出现对应译名 | warn |
| 残留原文 | 译文中出现大段未译英文（启发式） | warn |
| 占位符丢失 | 原文的标记/占位符在译文中缺失 | warn |

结果存内存/临时表，前端在工作台以角标呈现，点击跳到对应段。

---

## 10. API 清单（REST + SSE）

```
# 书目 / 工程
POST   /api/projects                上传文件+建工程（multipart）→ 触发解析+分段
GET    /api/projects                列表（含进度：done/total）
GET    /api/projects/{id}           详情 + 章节树 + 统计
DELETE /api/projects/{id}           删除工程与数据
PATCH  /api/projects/{id}           改 provider_cfg / 标题

# 翻译控制
POST   /api/projects/{id}/translate 启动/续跑（只跑 pending/error）
POST   /api/projects/{id}/stop      停止（协作式取消）

# 分段（工作台核心）
GET    /api/projects/{id}/segments  分页/按章/按状态过滤，虚拟列表用
PATCH  /api/segments/{id}           编辑译文 / 标记 reviewed
POST   /api/segments/{id}/retranslate  单段重译

# 术语表
GET    /api/projects/{id}/glossary
POST   /api/projects/{id}/glossary       增/批量导入(csv)
PATCH  /api/glossary/{id}
DELETE /api/glossary/{id}

# 导出 / QA
POST   /api/projects/{id}/export    body=导出选项 → 返回文件下载路径
GET    /api/projects/{id}/qa        跑 QA 返回问题列表

# 流式
GET    /api/projects/{id}/stream    SSE：{type: progress|segment_done|error, ...}
```

SSE 事件：`progress`(done/total/当前段)、`segment_done`(id, target 增量)、`chapter_summary`、`error`。

---

## 11. 前端结构

**三个页面**
1. **Library**：书目卡片（封面/标题/进度条/provider），上传入口，删除。
2. **Workbench**（核心）：
   - 顶部：章节下拉、状态过滤、进度、启动/停止翻译、导出。
   - 主体：TanStack Virtual 虚拟列表，每行 `SegmentRow` = 左原文 / 右译文。
   - 右侧栏：`GlossaryPanel` 术语速查/增改、QA 问题列表。
   - 单段交互：点右侧进编辑、保存(PATCH)、标记已校、重译。
   - SSE 实时刷新翻译中段落的状态与译文。
3. **Settings**：provider/model 选择、temperature、并发数、默认导出模式。

**数据层**：TanStack Query 管服务端状态；SSE 到达时 `invalidate`/局部 `setQueryData` 更新对应段，避免整表刷新。

---

## 12. 配置与密钥

- `.env`（gitignore）：各 provider 的 API key（`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / …），LiteLLM 自动读取。
- `.env.example`：占位模板，进仓库。
- `settings.toml`（可选）：默认 model、并发、上下文预算、导出默认值。
- `config.py` 用 Pydantic Settings 合并「环境变量 > settings.toml > 默认值」。
- **密钥只在后端进程内使用**，绝不下发前端；前端设置页只选 model 名与参数。
- 本地应用默认绑 `127.0.0.1`，不暴露公网；如需 auth 再议（当前个人本机场景可不加，但代码里留开关）。

---

## 13. 测试与验证策略

| 层 | 内容 |
|---|---|
| 解析 | 小 epub/md fixture → DocModel 块数、struct_path、translatable 正确 |
| 分段 | stable_key 唯一且重跑稳定；超长块软切/拼回无损 |
| 引擎 | mock provider：并发上限、重试退避、TM 命中、状态流转、断点续跑只补 pending/error |
| 回写 | 双语/纯译 epub 能被 lxml 重新解析；md token 数守恒；round-trip 不破结构 |
| QA | 构造漏译/长度异常/结构破损样本，规则命中正确 |
| API | httpx 打全流程：上传→解析→翻译(mock)→编辑→导出 |
| 真机 | 拿一本真实公版 epub（如古登堡计划），小模型跑通全链路，人工看质量 |

CI 不接外部 LLM：provider 一律 mock；真机试跑手动执行。

---

## 14. 成本 / 性能预估

- 一本 ~10 万英文词书 ≈ 13–15 万 tokens 输入，译文相近；来回约 30–40 万 tokens。
- 参考单价（会变，仅量级）：
  - 便宜档（deepseek/gemini-flash/本地 ollama）：单本几毛到几元人民币或零。
  - 高质档（claude/gpt-4o 级）：单本约数元到十几元。
- TM 复用与「只补 pending/error」显著降重复成本。
- 性能：并发 4 + 便宜模型，一本书数十分钟内可完；瓶颈是 provider 限速而非本地。
- 前端翻译前给**成本预估**（token 估算 × 单价表），Phase 4 完善。

---

## 15. 分阶段路线图（含任务与验收）

### Phase 0 — 骨架
**任务**
- 建目录结构（§2）、`pyproject.toml`、`.env.example`、`.gitignore`。
- FastAPI 骨架 + `/health`；SQLModel 建表 + Alembic 初始迁移。
- `config.py`（Settings）；前端 Vite+TS+Tailwind 脚手架 + Library 空页。

**验收**：`uvicorn` 起服务、`/health` 通、DB 文件生成、前端 `dev` 能开、四表建成。

### Phase 1 — MVP 翻译引擎（命令行可验证）★做完即能用
**任务**
- `parsers`（epub+md → DocModel）、`segment`（建账本+stable_key）。
- `providers/litellm_provider`、`engine/translator`（并发+重试+TM+状态机）、
  `context` + `prompt`（术语+滚动上下文，摘要可先留桩）。
- `writers`（双语/纯译 epub + md 回写）。
- `cli.py`：`translate <file> --model ... --out ...` 全链路跑通。
- 单测覆盖 parsers/segment/engine(mock)/writers。

**验收**：
- 一条命令把真实 epub 译成双语 epub，能在阅读器打开、排版不崩。
- 中断后重跑只补 `pending/error`，已 done 不重复调用。
- md 输入 round-trip 结构守恒。

### Phase 2 — Web 工作台
**任务**
- API：projects（上传/列表/详情/删除/patch）、translate/stop、segments 分页/编辑/重译、SSE stream。
- 前端：Library（上传+进度）、Workbench（虚拟列表左右对照+编辑+标记+重译+SSE 实时）、Settings。

**验收**：浏览器里上传→翻译→看进度→逐段编辑/重译/标记→导出，全程不碰命令行。

### Phase 3 — 精修增强
**任务**
- 术语表 CRUD + csv 导入 + 工作台速查；命中注入 prompt。
- 翻译记忆面板（命中率/条目）；章节摘要真正生成并缓存。
- QA（§9）接入工作台角标 + 跳转；导出选项（双语/纯译/含未译）。

**验收**：术语在长篇里前后一致；QA 能定位漏译/结构问题；导出选项生效。

### Phase 4 — 打磨
**任务**：快捷键（下一段/保存/重译/标记）、暗色模式、翻译前成本预估、批量重译/批量标记、导出模板微调。

**验收**：审校顺手、成本可预期、常用操作有快捷键。

---

## 16. 针对书籍翻译的关键设计点

- **双语交错输出**是个人阅读体感最好的形态，作为默认导出。
- **术语表 + 滚动上下文 + 章节摘要**解决长篇一致性（人名/地名/概念前后统一），这是散装脚本翻不好书的主因。
- **账本式续跑**：几十万 token 的书难免中断/超限，务必断点续译，只补 `pending/error`。
- **只译文本、不动结构**：epub 保标签、md 保代码块与格式，回写靠 `stable_key + struct_path`，避免排版崩坏。
- **TM 跨书复用**：重复段零成本，长期省钱。

---

## 17. 参考项目

- [yihong0618/bilingual_book_maker](https://github.com/yihong0618/bilingual_book_maker) — 翻译引擎参考（epub/txt/md/srt/pdf，LiteLLM 多 provider，上下文/续跑/并行）
- [Supervertaler-Workbench](https://github.com/Supervertaler/Supervertaler-Workbench) — 工作台 UI 参考（TM、术语、多 LLM、并排概念检索）
- [Piotr-Grechuta/epub-translator-studio](https://github.com/Piotr-Grechuta/epub-translator-studio) — 状态账本与 QA 门槛参考
- [jb41/translate-book](https://github.com/jb41/translate-book)、[clcreuso/The_Babel_Library](https://github.com/clcreuso/the-babel-library)、[nguyenvanduocit/epubtrans](https://github.com/nguyenvanduocit/epubtrans) — 轻量实现参考

---

## 18. 下一步

先落地 **Phase 0 + Phase 1**：
1. 搭目录结构与依赖（§2、§1）。
2. 建 SQLite schema（§3）+ Alembic。
3. 实现 parsers → segment → translator → writers 最小闭环（§4–§8）。
4. 用 `cli.py` 拿一本真实公版 epub 试跑，验证双语输出质量与断点续跑。
5. 质量达标后再叠加 Phase 2 Web 工作台。

> 建议第一次试跑用便宜/本地模型（deepseek / ollama qwen）压成本，质量摸底后再上高质档。







