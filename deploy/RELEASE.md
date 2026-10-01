# 服务器端更新与降级

版本模型：**源码归档 + 服务器端构建的版本化镜像**。每次发版用 `git archive`
打一个源码包，服务器上的 `deploy/release.py` 负责解压、构建 `trans-linux:<版本>`、
数据库迁移、切换镜像 tag、健康检查；回滚 = 切换回旧 tag（必要时先用最新镜像
自动 `alembic downgrade`）。应用内「设置 → 系统与更新」卡片只读展示版本、
schema 状态、发布历史与命令，不在界面内执行任何变更。

## 发版打包（在开发机）

```bash
git tag -a v1.1.0 -m "v1.1.0"
git archive --format=tar.gz --prefix=src/ v1.1.0 -o trans-1.1.0.tar.gz
git rev-parse v1.1.0 > git_sha.txt
```

`--prefix=src/` 让归档顶层是 `src/Dockerfile`，release.py 能直接识别。
上传到服务器（`/opt/trans-linux` 旁，例如 `/opt/releases/trans-1.1.0.tar.gz`）。
该目录**不是 git 仓库**，`git archive` 只打包已跟踪文件，不会带入/覆盖
`.env.production` 与 `/etc/trans` 密钥。

## 服务器端命令（在 compose 目录旁执行）

```bash
# 首次使用：探测当前运行版本/迁移头，写 .releases/manifest.json（幂等）
python3 deploy/release.py init

# 安装新版本：备份 → 用新镜像迁移 DB → 切 TRANS_IMAGE_TAG → up → 健康检查
python3 deploy/release.py install /opt/releases/trans-1.1.0.tar.gz --git-sha <sha>

# 回滚（缺省为最近安装的前一版本）：备份 → 降级 DB → 切 tag → up → 健康检查
python3 deploy/release.py rollback
python3 deploy/release.py rollback 1.0.0

# 只读查看
python3 deploy/release.py status
python3 deploy/release.py history
```

参数：

- `--compose-file`（默认 `compose.yml`）、`--override-file`（默认
  `compose.6020.yml`，不存在则忽略）、`--env-file`（默认 `.env.production`）。
- `install`：`--version` 显式指定版本号（否则从归档名 `trans-<版本>.*` 或归档内
  `version.json` 推断）；`--git-sha` 写入镜像的 commit（默认 `unknown`）。
- `--no-migrate`：跳过自动迁移/降级（手工切换后自己跑迁移的逃生舱）。

## 目录与产物

```
.releases/
  manifest.json          # 每个版本: version/git_sha/schema_revision/installed_at/tag；operations 保留最近 50 条
  lock                   # O_EXCL 运行锁，防止并发安装/回滚
  backups/backup-<ts>-<版本>.tar.gz   # trans-state 卷的只增 tar 备份
  <版本>/src/            # 解压后的源码（Dockerfile 在此）
```

状态文件位于 compose 目录下，不进 git（`.gitignore` 已忽略 `.releases/`）。

## 流程细节

1. **install**：解压 → 校验 `src/Dockerfile` → `docker build -t trans-linux:<版本>`
   （写入 VERSION/GIT_SHA/BUILD_TIME，烘焙进 `/app/version.json`）→ 读取新镜像迁移头 →
   停止 app，等待 SQLite 写入与任务退出 → **对 trans-state 卷做 tar 备份**
   （首装也备份，缺失/0 字节则硬中止）→ 用**新镜像** `alembic upgrade head` →
   原子改写 `.env.production` 的 `TRANS_IMAGE_TAG` → `up -d app` → 轮询健康检查
   （超时 90s，并从实际运行容器的版本文件核对 git_sha）。任何一步失败：自动回滚（若 DB 已变则用新镜像
   `downgrade` 回旧迁移头 → 切回旧 tag → up → 健康检查），回滚也失败则打印备份路径。
2. **rollback**：停止 app → 备份 → 若目标 schema_revision ≠ 当前 DB 修订，用**当前镜像**
   `alembic downgrade <目标修订>`（先降级再切 tag）→ 切 tag → up → 健康检查 →
   目标容器 entrypoint 的 `upgrade head` 为幂等 no-op。失败先停止目标容器，
   恢复原 schema 修订，再切回原镜像并启动。
3. **健康检查**：compose healthcheck（python urllib GET `/health/live`）判定
   healthy/unhealthy；通过 `docker exec` 读取运行容器的 `/app/version.json` 核对
   git_sha，避免端口未发布或 HTTPS 导致校验被跳过。

## 安全护栏（代码强制）

- 目标版本 == 当前版本 → 拒绝执行。
- 任何迁移/tag 切换前必须有非空备份，否则硬中止。
- **绝不** `down --volumes`；**绝不**写 `/etc/trans`（master-key 不动）。
- 不因 `TRANS_ENVIRONMENT=development` 拒绝执行（6020 主机即 dev 模式）。

## 迁移与备份恢复

- 发布策略：只允许**可逆新增**迁移（建表/加索引等）。0005 起支持降级。
- DB 迁移由 release.py 显式执行；生产 `TRANS_RUN_MIGRATIONS_ON_STARTUP=false`，
  entrypoint 的 `upgrade head` 只是幂等 no-op。
- 最坏情况恢复：用最新镜像 `alembic downgrade` 回旧迁移头 → 切回旧 tag →
  再不行就从 `.releases/backups/` 的 tar 恢复 trans-state 卷。
