# 未合并分支集成验证记录

日期：2026-10-01（Asia/Shanghai）。

## 输入与合并范围

- 原主线：`e1b877050be03e43643c498d1c71345bd9ce796d`。
- 集成分支：`codex/integrate-unmerged-branches`。
- 发布管理／日志界面：`claude/zen-roentgen-5c2d26`，`f7d1c15efe77167cd20d5008b8509068920c1495`。
- 模型内录入／复用密钥：`claude/eager-shannon-7b6f17`，`e55d5cb`。
- 已运行 `git fetch origin`；远端 main 没有额外提交。
- 其他本地功能分支均已是原 main 的祖先。
- `ed14655` 是临时 detached worktree 的提交，其日志实现与发布分支对应文件一致，不是独立待合并分支。

## 冲突处理与修复

保留主线的设置组件拆分、服务器表单独立结构和工作台动态视口／侧栏高度修复。
将旧分支的密钥录入、复用及无密钥模式移植到 `ModelProfilesSection`，保持密钥先加密、模型后绑定的调用顺序。
若密钥保存成功而模型保存失败，重试复用该凭据，避免重复创建；同时禁用重复提交并清空明文输入。

日志页清空时清除已加载历史，并刷新所有过滤条件对应的缓存；合并分页结果时按 ID 去重。

发布工具在备份／迁移前停止 app，避免复制正在写入的 SQLite 卷；首次安装也执行备份。
安装自动回滚和回滚失败恢复均先停止 app，再恢复 schema 和镜像。
git_sha 校验改为读取实际运行容器的版本文件，缺失或不匹配都导致失败。
更新了发布文档和 Docker E2E 的备份数断言。

真实容器验证发现受限 umask 解压会使迁移目录仅 root 可读，现已在 Dockerfile
显式规范应用资产读取权限。隔离 E2E 使用独立镜像仓库、项目和卷，清理前核对配置，
支持清理前次失败留下的隔离镜像标签。修复了密钥挂载目标、UID 权限和 Caddyfile 路径。
远程执行器统一使用 POSIX 路径、远端真实路径护栏及固定 SSH 主机指纹，并同时读取输出两条流。

## 已通过的验证

| 验证 | 结果 |
| --- | --- |
| Python 3.12.14 独立虚拟环境 + requirements.lock + dev 依赖 | 安装成功 |
| 后端全量 pytest（包含发布、日志及远程执行器回归） | 129 passed |
| 前端全量 Vitest（含密钥流程、日志分页／过滤／清空回归） | 12 passed |
| TypeScript + Vite 生产构建 | 通过 |
| Ruff：backend、deploy/release.py、deploy/run_e2e_on_server.py | 通过 |
| pip check | 通过 |
| bash -n：entrypoint.sh、test_release_e2e.sh | 通过 |
| git diff --check | 通过 |
| 服务器真实 Docker E2E | 47 passed，0 failed |

后端存在两条非失败警告：Starlette 的 httpx 测试接口弃用提示，以及 project/stored_artifact 相互外键引起的 SQLAlchemy 排序提示。
发布边界与隔离配置共 10 项测试，远程执行器 29 项离线测试；另有真实 Docker 端到端验证。

## Docker 验证与合并

用户授权在指定服务器验证及部署。Docker 28.0.1 / Compose 2.32.1 上运行冻结源码
`0f054c2`，隔离工作区 `/opt/trans-e2e-codex-20261001`，项目 `trans-e2e`，
独立卷 `trans-e2e_trans-state`，无发布端口，使用一次性密钥。
首次安装、升级到测试迁移 0006、降级至 0005、坏版本安装失败自动恢复等共 47 项通过。
隔离容器、卷和测试镜像已清理；4 个非空备份与 manifest 留存服务器供复查。
本地完整日志：`work/remote-e2e.log`。

验证门禁已满足，以 fast-forward 将集成分支合入 main，保留功能分支便于追溯。
正式部署继续保留原密钥与状态卷；旧镜像通过不可变 ID 登记为 legacy 回退版本，
部署前保存原源码、配置和停止写入后的状态卷备份。

## 正式部署与线上复核

部署站点：`http://121.4.28.63:6020/`；部署版本 `2026.10.01-657a39d`，
代码提交 `657a39d7a7474ae4f11f257369fad4ce5e44524a`。
部署完成后仅更新本验证文档，应用代码未改变。

- app 与 Caddy 均 healthy；运行镜像、环境 tag、manifest 当前版本一致。
- 数据库从 `0004_runtime_logs` 升级至 `0005_release_record`，与代码迁移头一致，SQLite integrity_check 为 ok。
- 切换前停机备份 `backup-20261001-111116-2026.10.01-657a39d.tar.gz`，70,495,153 字节。
  迁移前解出数据库验证完整性、旧 schema、四张原数据表的行数与完整摘要；备份及源配置仅 root 可访问。
- 线上完成管理员登录、未认证访问拒绝、缺失 CSRF 写操作拒绝、上传解析、真实模型翻译、
  日志过滤／分页／清空、双语 Markdown 导出及下载、退出登录。
  仅使用两段极短的专属测试文本，测试项目与文件已清理。
- 浏览器确认新增模型的密钥录入及复用模式、无嵌套 form、系统版本与 schema 状态、
  原书工作台、运行日志页过滤及历史加载（100 → 200 条）。
- 原 project（5 行）、segment（12,809 行）、model_profile（2 行）、provider_credential（1 行）
  整表 SHA-256 摘要均与升级前一致，包含原正文与译文。
- 更新后的服务日志没有 ERROR 或异常堆栈；当前 secret 文件权限和 UID 保持原状态。
- 原镜像 `sha256:7de5aacead9933f9735b4a9ca8fa7db70c1e62beff044875bcc01ec8ce322478`
  保留为 `trans-linux:legacy-20261001-7de5aace`。

服务器回退入口（仅供需要时运行，本次未对正式服务执行回退）：

```sh
cd /opt/trans-linux
umask 077
python3 deploy/release.py rollback legacy-20261001-7de5aace
```

原源码与配置：`/opt/trans-deploy-20261001/pre-upgrade/source.tar.gz`。
本地过程记录：`work/remote-prod-install.log`、`work/server-smoke.log`、
`work/remote-prod-verify.log`；截图 `work/deployment-settings.jpg`。

## 首轮浏览中的既有观察

主线书目的 temperature 会覆盖模型 generation_params 中的 temperature（局部请求构造验证：1.1 被覆盖为 0.3）。
模型的 max_output_tokens/context_window_tokens 字段目前保存并返回，但未接入翻译请求的输出限制／窗口校验。
这些行为在待合并分支之前已经存在，未改变其参数优先级语义；可作为后续配置契约修复任务。
