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

## 已通过的验证

| 验证 | 结果 |
| --- | --- |
| Python 3.12.14 独立虚拟环境 + requirements.lock + dev 依赖 | 安装成功 |
| 后端全量 pytest（包含新增发布与日志接口回归） | 99 passed，165.27 秒 |
| 前端全量 Vitest（含密钥流程、日志分页／过滤／清空回归） | 12 passed |
| TypeScript + Vite 生产构建 | 通过 |
| Ruff：backend、deploy/release.py | 通过 |
| pip check | 通过 |
| bash -n：entrypoint.sh、test_release_e2e.sh | 通过 |
| git diff --check | 通过 |

后端存在两条非失败警告：Starlette 的 httpx 测试接口弃用提示，以及 project/stored_artifact 相互外键引起的 SQLAlchemy 排序提示。
发布流程的 Docker 调用边界在 9 项回归测试中使用模拟；这不等于真实容器端到端验证。

## 合入 main 前的剩余门禁

本机 Windows 和 Ubuntu-24.04 WSL 均未安装 Docker。真实 Docker E2E 尚未执行。
已向用户询问：在本机 WSL 安装 Docker、使用用户指定服务器的隔离项目，或确认本次验证仅要求现有测试全量通过。
收到选择后执行相应验证；通过后才能将集成分支合入 main。
main 保持原提交，本记录不代表合并或推送已完成。

## 首轮浏览中的既有观察

主线书目的 temperature 会覆盖模型 generation_params 中的 temperature（局部请求构造验证：1.1 被覆盖为 0.3）。
模型的 max_output_tokens/context_window_tokens 字段目前保存并返回，但未接入翻译请求的输出限制／窗口校验。
这些行为在待合并分支之前已经存在，未改变其参数优先级语义；可作为后续配置契约修复任务。
