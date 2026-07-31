# 墨译 · AI 翻译工作台

一个面向英文书籍的本地翻译与审校工作台。它支持 EPUB 和 Markdown，能够调用
LiteLLM 兼容的云端或本地模型，保存逐段翻译进度，并导出双语或纯译文版本。

> [!WARNING]
> **生产部署需要完整的安全验证。** 此工作台已实现管理员认证、会话管理、CSRF 防护、
> 密钥加密存储和审计日志等安全基础设施，但尚未完成完整的安全评估。在可信内网环境外
> 部署前，必须验证：首次启动的管理员引导流程、主密钥轮换与恢复演练、登录限流、会话
> 有效期策略、密钥权限隔离、备份与恢复流程。建议先在可信网络中评估，完成安全审查后
> 再向公网暴露。

## 已实现能力

- EPUB / Markdown 结构化解析，只翻译可见文本并保留排版结构
- 多模型切换、受控并发、指数退避重试及断点续跑
- 滚动上下文、术语表、章节摘要和跨项目翻译记忆
- 逐段编辑、重译、审核标记、批量操作与键盘快捷键
- 实时翻译进度、翻译前成本估算和 QA 问题定位
- 双语或纯译文 EPUB / Markdown 导出
- 源文件、密钥和数据库默认仅保存在本机

## Windows 本地开发

本地开发需要 Python 3.11+ 与 Node.js 20+。以下命令适用于 PowerShell；开发服务仅绑定
回环地址，不使用生产 Compose 或 Caddy。

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Set-Location frontend
npm.cmd install
Set-Location ..
.\run.ps1
```

打开 `http://127.0.0.1:5173`。后端接口位于 `http://127.0.0.1:8000`，交互式 API
文档位于 `/docs`。

在 `.env` 中填写所用模型服务的密钥；使用 Ollama 等本地模型时通常不需要云端密钥。
密钥只由后端读取，不会通过 API 返回给浏览器。选择云端模型时，待译段落与必要上下文会
发送给所选模型服务商；需要完全离线时请使用 Ollama 等本地 provider。注意容器内的
`localhost` 不是 Linux 宿主机；生产使用 Ollama 时必须配置一个容器可达的服务地址和网络。

### 命令行

不启动前端也可以完成一次翻译：

```powershell
.\.venv\Scripts\python.exe -m backend.cli translate .\book.epub `
  --model openai/gpt-5-mini `
  --out .\exports\book-bilingual.epub
```

运行 `python -m backend.cli --help` 查看模型参数、导出模式和续跑选项。

### 开发验证

```powershell
# 后端
.\.venv\Scripts\python.exe -m pytest

# 前端类型检查与生产构建
Set-Location frontend
npm.cmd run build
```

## Linux 生产部署脚手架

此路径面向单机 Linux + Docker Compose，使用 Node.js 22 构建前端，使用 Python 3.12
运行一个 Uvicorn worker，并由 Caddy 提供同源 HTTPS、反向代理和 SSE 转发。只有 Caddy
发布 80/443；应用的 8000 端口仅在 Compose 网络内可见。`/app` 可保持只读，SQLite、
上传文件和导出文件写入持久化的 `/var/lib/trans` 卷。

### 前置条件

- Linux 主机、Docker Engine 与 Docker Compose v2
- 指向该主机的域名；公网 TLS 场景需允许入站 TCP 80/443 和 UDP 443
- 两个不同的高熵 secret 文件，且宿主机权限建议为 `0600`
- 已完成上方警告列出的安全门禁；否则仅限可信私网或本机评估

### 配置与启动

```sh
cp .env.production.example .env.production
install -d -m 0700 /etc/trans
umask 077
openssl rand -base64 32 > /etc/trans/admin-bootstrap-password
# 主密钥契约为精确的 32 个原始字节，不要在此处使用 base64。
openssl rand 32 > /etc/trans/master-key
# 编辑 .env.production：设置域名、HTTPS origin、exact trusted host、管理员用户名和
# 两个绝对 secret 路径。Provider 凭据应由后端加密存入数据库，不写入生产环境变量。

docker compose --env-file .env.production -f compose.yml config
docker compose --env-file .env.production -f compose.yml build --pull
docker compose --env-file .env.production -f compose.yml up -d

docker compose --env-file .env.production -f compose.yml ps
docker compose --env-file .env.production -f compose.yml logs --tail=100 app caddy
```

生产配置不把 secret 值写入 Compose 或镜像：`admin_bootstrap_password` 与 `master_key`
以 Docker secret 文件挂载到 `/run/secrets/`。入口脚本会确认两者存在、非空、可读且权限
受限，然后在 `/app` 执行 Alembic `upgrade head`，成功后以 PID 1 启动且只启动一个
Uvicorn worker。迁移失败会阻止服务启动，避免在未知 schema 上提供流量。应用和 Caddy
均以 `/health/live` 做存活检查；该端点不检查数据库或存储，因此文档不将其称为 readiness。

停止服务但保留状态：

```sh
docker compose --env-file .env.production -f compose.yml down
```

命名卷 `trans-state` 保存 `/var/lib/trans/trans.db` 以及 `uploads/`、`exports/`、`tmp/`
和 `backups/`；`caddy-data` 保存证书与 Caddy 状态，`caddy-config` 保存 Caddy 运行配置。
**不要**在普通升级时添加 `--volumes`。升级和迁移前应备份这些卷，并定期执行恢复演练。
JSON 容器日志启用大小与文件数轮换；Caddy 访问日志删除完整请求 URI（因此不会记录查询
字符串），并移除授权、Cookie 和 Set-Cookie 头；运维方仍应控制日志访问与保留期。

### 当前生产集成门禁

以下能力已落地并由 Compose 按生产约定提供；上线前仍需完成下方列出的运维与安全验证：

- 管理员首次启动引导：数据库无管理员时，`initialize_admin` 从
  `TRANS_ADMIN_BOOTSTRAP_PASSWORD_FILE`（Docker secret）读取密码创建唯一管理员；
  一旦存在管理员，该文件会被忽略，旧部署 secret 无法静默重置账号。登录后请立即修改
  默认引导密码。
- 凭据加密：`TRANS_MASTER_KEY_FILE`（精确 32 原始字节的 secret 文件）由后端读取，
  用 AES-GCM 加密 Provider API Key（AAD 绑定凭据 ID、Provider 与密钥版本），API 只
  返回掩码。开发环境未配置主密钥时会在状态目录自动生成临时密钥，生产环境必须显式
  配置。
- `TRANS_ENVIRONMENT=production`、绝对 `TRANS_STATE_DIR`、`TRANS_FRONTEND_DIST`、
  `TRANS_PUBLIC_ORIGIN`、exact `TRANS_TRUSTED_HOSTS`、空 CORS 与
  `TRANS_RUN_MIGRATIONS_ON_STARTUP=false` 已由 Compose 提供。入口脚本是生产环境
  唯一迁移者，应用进程不会再次执行迁移；保持单副本且不要使用 `--scale app`。
- 反向代理信任范围默认保持为 `127.0.0.1` 占位值。部署时应给 Compose 网络固定子网并将
  `TRANS_FORWARDED_ALLOW_IPS` 收窄到 Caddy 地址或可信 CIDR，之后再依赖客户端 IP 做安全
  判断。

上线前必须补齐的运维验证（代码已实现，但需演练确认）：

- 主密钥备份与轮换恢复演练：密钥丢失后已存凭据将无法解密。
- 管理员密码修改与恢复路径、登录限流与会话失效策略的实际验证。
- 登录失败限流、审计日志保留与访问控制、备份与恢复演练。
- 依赖与镜像扫描、`compose.6020.yml` 等非标准覆盖配置的移除或加固。

## 运行时数据

Windows 本地开发的内容位于 `data/` 和 `exports/`，默认不会提交到版本库。Linux
Compose 将对应内容持久化到 `trans-state` 卷中的 `/var/lib/trans`。删除项目时只会删除
该项目登记的上传源文件及数据库记录；请自行备份需要长期保留的导出书稿。

## 目录

- `backend/app/parsers`：EPUB / Markdown 解析
- `backend/app/engine`：上下文、提示词、翻译编排和翻译记忆
- `backend/app/writers`：双语及纯译文回写
- `backend/app/api`：REST 与 SSE 接口
- `frontend/src`：书库、翻译工作台和设置
- `backend/tests`：解析、引擎、回写、QA 与 API 测试
- `Dockerfile`、`compose.yml`、`deploy/`：Linux 生产镜像、编排与 Caddy 配置

完整设计说明见 [plan.md](./plan.md)。
