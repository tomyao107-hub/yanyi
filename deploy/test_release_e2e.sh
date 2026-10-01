#!/usr/bin/env bash
#
# 服务器端 E2E：在完全隔离的 compose 项目（trans-e2e）上验证 deploy/release.py 的
# init / install / rollback / status / history，以及"坏版本安装 → 健康检查超时 →
# 自动回滚"失败路径。
#
# 隔离原则（硬保证，代码层面对齐 release.py 的护栏）：
#   - 所有状态限制在 $WORKSPACE（默认 /opt/trans-e2e-workspace）与 trans-e2e_* 命名空间
#   - 启动即核对 compose 项目名 == trans-e2e、卷名 == trans-e2e_trans-state，否则拒绝继续
#   - 绝不触碰 trans-production 栈、trans-production_trans-state 卷、/etc/trans 密钥
#   - 密钥为一次性 throwaway（openssl rand 生成），与生产 master-key 无关
#
# 用法（在服务器上，root）：
#   E2E_WORKSPACE=/opt/trans-e2e-workspace bash test_release_e2e.sh
set -euo pipefail

WORKSPACE="${E2E_WORKSPACE:-/opt/trans-e2e-workspace}"
WORKSPACE="$(realpath -m "$WORKSPACE")"
case "$WORKSPACE" in
  /opt/trans-e2e-*) ;;
  *) echo "错误: E2E_WORKSPACE 必须位于独立的 /opt/trans-e2e-* 目录"; exit 1 ;;
esac
SRCBASE="$WORKSPACE/srcbase"
E2E_DIR="$WORKSPACE/e2e"
RELEASES="$WORKSPACE/releases"
RELEASE_PY="$SRCBASE/deploy/release.py"
VOLUME="trans-e2e_trans-state"
IMAGE="trans-e2e-release-$(basename "$WORKSPACE")"
export TRANS_RELEASE_IMAGE="$IMAGE"
unset COMPOSE_PROJECT_NAME TRANS_PROJECT_NAME TRANS_VOLUME_NAME
PUBLIC_ORIGIN="http://127.0.0.1:18080"

PASSED=0
FAILED=0

say()  { printf '\n[E2E] %s\n' "$*"; }
pass() { PASSED=$((PASSED + 1)); printf 'PASS: %s\n' "$*"; }
fail() { FAILED=$((FAILED + 1)); printf 'FAIL: %s\n' "$*"; }

assert_eq() {
  local desc="$1" actual="$2" expected="$3"
  if [ "$actual" = "$expected" ]; then pass "$desc"; else fail "$desc（期望 '$expected'，实际 '$actual'）"; fi
}
assert_contains() {
  local desc="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) pass "$desc" ;;
    *) fail "$desc（未找到 '$needle'）" ;;
  esac
}

# ---- 前置检查 -------------------------------------------------------------
docker info >/dev/null 2>&1 || { echo "错误: docker 不可用"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "错误: docker compose v2 不可用"; exit 1; }
[ -f "$RELEASE_PY" ] || { echo "错误: 缺少 $RELEASE_PY（先把仓库解压到 $SRCBASE）"; exit 1; }
python3 -c "import tarfile; print(tarfile.TarFile.extractall.__doc__)" >/dev/null 2>&1 || true

say "工作区: $WORKSPACE  源基座: $SRCBASE"

# ---- 构建隔离环境 ---------------------------------------------------------
rm -rf "$E2E_DIR" "$RELEASES" "$WORKSPACE/v110" "$WORKSPACE/v120bad"
mkdir -p "$E2E_DIR/secrets" "$E2E_DIR/deploy" "$RELEASES"

cp "$SRCBASE/compose.yml" "$E2E_DIR/compose.yml"
cp "$SRCBASE/deploy/Caddyfile" "$E2E_DIR/deploy/Caddyfile"

umask 077
if command -v openssl >/dev/null 2>&1; then
  openssl rand 32 > "$E2E_DIR/secrets/master-key"
  openssl rand -base64 24 > "$E2E_DIR/secrets/admin-bootstrap-password"
else
  head -c 32 /dev/urandom > "$E2E_DIR/secrets/master-key"
  head -c 32 /dev/urandom | base64 > "$E2E_DIR/secrets/admin-bootstrap-password"
fi
chown 10001:10001 "$E2E_DIR/secrets/master-key" "$E2E_DIR/secrets/admin-bootstrap-password"
chmod 600 "$E2E_DIR/secrets/master-key" "$E2E_DIR/secrets/admin-bootstrap-password"

cat > "$E2E_DIR/.env.e2e" <<ENV
TRANS_ENVIRONMENT=development
TRANS_IMAGE_TAG=1.0.0
TRANS_VERSION=dev
TRANS_GIT_SHA=unknown
TRANS_BUILD_TIME=unknown
TRANS_DATABASE_URL=sqlite:////var/lib/trans/trans.db
TRANS_UPLOAD_DIR=./data/uploads
TRANS_EXPORT_DIR=./exports
TRANS_RUN_MIGRATIONS_ON_STARTUP=false
TRANS_TRUSTED_HOSTS=["127.0.0.1","localhost","testserver","app"]
TRANS_CORS_ORIGINS=[]
TRANS_SITE_ADDRESS=trans-e2e.local
TRANS_PUBLIC_ORIGIN=$PUBLIC_ORIGIN
TRANS_ADMIN_USERNAME=admin
TRANS_ADMIN_BOOTSTRAP_PASSWORD_SECRET_FILE=$E2E_DIR/secrets/admin-bootstrap-password
TRANS_MASTER_KEY_SECRET_FILE=$E2E_DIR/secrets/master-key
ENV

# 覆盖：去掉 caddy 发布端口（本机自检即可），绑定密钥为 throwaway 文件
cat > "$E2E_DIR/compose.e2e.yml" <<'YAML'
name: trans-e2e

services:
  app:
    image: "${TRANS_RELEASE_IMAGE}:${TRANS_IMAGE_TAG:-latest}"
    environment:
      TRANS_ENVIRONMENT: development
      TRANS_PUBLIC_ORIGIN: http://127.0.0.1:18080
      TRANS_TRUSTED_HOSTS: '["127.0.0.1","localhost","app"]'
    secrets: !override []
    volumes: !override
      - type: bind
        source: ./secrets/master-key
        target: /run/secrets/master_key
        read_only: true
      - type: bind
        source: ./secrets/admin-bootstrap-password
        target: /run/secrets/admin_bootstrap_password
        read_only: true
      - type: volume
        source: trans-state
        target: /var/lib/trans

  caddy:
    ports: !override []
    depends_on: !override
      app:
        condition: service_started
YAML

cd "$E2E_DIR"
COMPOSE_BASE=(docker compose -p trans-e2e --env-file .env.e2e -f compose.yml -f compose.e2e.yml)
RELEASE=(python3 "$RELEASE_PY" --compose-file compose.yml --override-file compose.e2e.yml --env-file .env.e2e)

# ---- 隔离守卫：项目名/卷名核对，绝不指向生产栈 ----------------------------
CONFIG_JSON="$("${COMPOSE_BASE[@]}" config --format json)"
PROJECT_NAME="$(printf '%s' "$CONFIG_JSON" | python3 -c "import json,sys; c=json.load(sys.stdin); print(c.get('name') or c.get('project'))")"
VOLUME_NAME="$(printf '%s' "$CONFIG_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['volumes']['trans-state']['name'])")"
assert_eq "隔离项目名" "$PROJECT_NAME" "trans-e2e"
assert_eq "隔离卷名" "$VOLUME_NAME" "trans-e2e_trans-state"
[ "$PROJECT_NAME" = "trans-e2e" ] || { fail "拒绝继续: 项目名非 trans-e2e"; exit 1; }
[ "$VOLUME_NAME" = "$VOLUME" ] || { fail "拒绝继续: 卷名非 trans-e2e_trans-state"; exit 1; }
printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys,os; c=json.load(sys.stdin); assert c["services"]["app"]["image"].startswith(os.environ["TRANS_RELEASE_IMAGE"]+":"), "wrong app image"; assert not any(s.get("ports") for s in c["services"].values()), "E2E must not publish ports"'

# 配置核对后再清理上次失败的残留（仅限 trans-e2e 命名空间）
"${COMPOSE_BASE[@]}" down --remove-orphans >/dev/null 2>&1 || true
docker volume rm "$VOLUME" >/dev/null 2>&1 || true

# ---- 打包假版本归档 -------------------------------------------------------
# 从 SRCBASE（当前工作树快照）复制出 v1.0.0；在其上追加 0006 迁移为 v1.1.0；
# 在 v1.1.0 基础上破坏 entrypoint 为 v1.2.0-bad。
cp -a "$SRCBASE/." "$WORKSPACE/v110/"
cp -a "$WORKSPACE/v110" "$WORKSPACE/v120bad"

cat > "$WORKSPACE/v110/backend/alembic/versions/0006_e2e_probe.py" <<'PY'
"""E2E probe table (additive, reversible)."""
from alembic import op

revision = "0006_e2e_probe"
down_revision = "0005_release_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE e2e_probe (id INTEGER PRIMARY KEY, note TEXT)")


def downgrade() -> None:
    op.execute("DROP TABLE e2e_probe")
PY

sed -i 's/^SCHEMA_HEAD_REVISION = "0005_release_record"/SCHEMA_HEAD_REVISION = "0006_e2e_probe"/' \
  "$WORKSPACE/v110/backend/app/db.py"

printf '#!/bin/sh\nexit 1\n' > "$WORKSPACE/v120bad/deploy/entrypoint.sh"

# 归档统一加 src/ 顶层前缀，与 RELEASE.md 的打包方式一致
tar -czf "$RELEASES/trans-1.0.0.tar.gz" -C "$SRCBASE" --transform 's|^|src/|' .
tar -czf "$RELEASES/trans-1.1.0.tar.gz" -C "$WORKSPACE/v110" --transform 's|^|src/|' .
tar -czf "$RELEASES/trans-1.2.0-bad.tar.gz" -C "$WORKSPACE/v120bad" --transform 's|^|src/|' .
say "假版本归档已生成:"
ls -la "$RELEASES"

# ---- 容器/数据库/健康辅助函数 ----------------------------------------------
app_container() { "${COMPOSE_BASE[@]}" ps -q app; }
container_tag() {
  local cid
  cid="$(app_container)"
  docker inspect -f '{{index .Config.Image}}' "$cid" | sed 's/^.*://'
}
health_status() {
  local cid
  cid="$(app_container)"
  docker inspect -f '{{.State.Health.Status}}' "$cid"
}
db_rev() {
  docker run --rm --network none -v "$VOLUME:/var/lib/trans" --entrypoint python \
    "$IMAGE:1.0.0" -c '
import os, sqlite3
p = "/var/lib/trans/trans.db"
if not os.path.exists(p):
    print("__MISSING__")
else:
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        print(row[0] if row else "")
    finally:
        con.close()
'
}
table_exists() {
  local table="$1"
  docker run --rm --network none -v "$VOLUME:/var/lib/trans" --entrypoint python \
    "$IMAGE:1.0.0" -c "
import sqlite3
p = '/var/lib/trans/trans.db'
con = sqlite3.connect('file:%s?mode=ro' % p, uri=True)
try:
    row = con.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name=?\", ('$table',)).fetchone()
    print('yes' if row else 'no')
finally:
    con.close()
"
}
health_git_sha() {
  local cid
  cid="$(app_container)"
  docker exec "$cid" python -c "
import json, urllib.request
print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/live'))['git_sha'])
"
}
backup_count() {
  shopt -s nullglob
  local files=(.releases/backups/backup-*.tar.gz)
  printf '%s' "${#files[@]}"
}
backup_min_size() {
  shopt -s nullglob
  local files=(.releases/backups/backup-*.tar.gz) min=999999999
  for f in "${files[@]}"; do
    local s
    s="$(stat -c %s "$f")"
    [ "$s" -lt "$min" ] && min="$s"
  done
  printf '%s' "$min"
}
manifest_current() {
  python3 -c "import json; print(json.load(open('.releases/manifest.json'))['current'])"
}
env_tag() {
  python3 -c "
import re
line = next(l for l in open('.env.e2e') if l.startswith('TRANS_IMAGE_TAG='))
print(line.strip().split('=', 1)[1])
"
}

# ============================== E2E 主流程 ==================================
say "=== 1) release.py init ==="
"${RELEASE[@]}" init
[ -f .releases/manifest.json ] && pass "manifest.json 已生成" || fail "manifest.json 缺失"
assert_eq "init 后 current" "$(manifest_current)" "None"

say "=== 2) install 1.0.0（首装：备份初始卷，空卷建库）==="
"${RELEASE[@]}" install "$RELEASES/trans-1.0.0.tar.gz" --git-sha e2e-1000
assert_eq "env TRANS_IMAGE_TAG" "$(env_tag)" "1.0.0"
assert_eq "manifest current" "$(manifest_current)" "1.0.0"
assert_eq "容器镜像 tag" "$(container_tag)" "1.0.0"
assert_eq "容器健康" "$(health_status)" "healthy"
assert_eq "数据库迁移头" "$(db_rev)" "0005_release_record"
assert_eq "e2e_probe 表（不应存在）" "$(table_exists e2e_probe)" "no"
assert_eq "/health/live git_sha" "$(health_git_sha)" "e2e-1000"
assert_eq "首装备份数（应 1）" "$(backup_count)" "1"

say "=== 3) status（1.0.0）==="
STATUS_OUT="$("${RELEASE[@]}" status)"
assert_contains "status 项目名" "$STATUS_OUT" "trans-e2e"
assert_contains "status 版本行" "$STATUS_OUT" "1.0.0"
assert_contains "status schema 一致" "$STATUS_OUT" "schema 一致: 是"

say "=== 4) install 1.1.0（含 0006 迁移：备份 + 预迁移 + 切 tag）==="
"${RELEASE[@]}" install "$RELEASES/trans-1.1.0.tar.gz" --git-sha e2e-1100
assert_eq "env TRANS_IMAGE_TAG" "$(env_tag)" "1.1.0"
assert_eq "manifest current" "$(manifest_current)" "1.1.0"
assert_eq "容器镜像 tag" "$(container_tag)" "1.1.0"
assert_eq "容器健康" "$(health_status)" "healthy"
assert_eq "数据库迁移头" "$(db_rev)" "0006_e2e_probe"
assert_eq "e2e_probe 表（应存在）" "$(table_exists e2e_probe)" "yes"
assert_eq "/health/live git_sha" "$(health_git_sha)" "e2e-1100"
assert_eq "升级备份数（应 2）" "$(backup_count)" "2"

say "=== 5) status（1.1.0，schema 一致）==="
STATUS_OUT="$("${RELEASE[@]}" status)"
assert_contains "status schema 一致(1.1.0)" "$STATUS_OUT" "schema 一致: 是"
assert_contains "status 当前版本" "$STATUS_OUT" "1.1.0"

say "=== 6) rollback 1.0.0（DB 降级 + 切回旧 tag）==="
"${RELEASE[@]}" rollback 1.0.0
assert_eq "env TRANS_IMAGE_TAG" "$(env_tag)" "1.0.0"
assert_eq "manifest current" "$(manifest_current)" "1.0.0"
assert_eq "容器镜像 tag" "$(container_tag)" "1.0.0"
assert_eq "容器健康" "$(health_status)" "healthy"
assert_eq "数据库迁移头（已降级）" "$(db_rev)" "0005_release_record"
assert_eq "e2e_probe 表（应已删除）" "$(table_exists e2e_probe)" "no"
assert_eq "/health/live git_sha" "$(health_git_sha)" "e2e-1000"
assert_eq "回滚后备份数（应 3）" "$(backup_count)" "3"

say "=== 7) history ==="
HISTORY_OUT="$("${RELEASE[@]}" history)"
assert_contains "history 含 install" "$HISTORY_OUT" "install"
assert_contains "history 含 rollback" "$HISTORY_OUT" "rollback"
assert_contains "history 含 1.1.0" "$HISTORY_OUT" "1.1.0"

say "=== 8) install 1.2.0-bad（entrypoint 崩溃 → 健康检查超时 → 自动回滚）==="
# 该步骤耗时约 90s（HEALTH_TIMEOUT）；期望命令失败并自动回滚回 1.0.0
BAD_OUT=""
if BAD_OUT="$("${RELEASE[@]}" install "$RELEASES/trans-1.2.0-bad.tar.gz" --git-sha e2e-1200 2>&1)"; then
  fail "坏版本安装本应失败"
else
  pass "坏版本安装失败（触发自动回滚）"
fi
assert_eq "坏版本后 env TRANS_IMAGE_TAG" "$(env_tag)" "1.0.0"
assert_eq "坏版本后 manifest current" "$(manifest_current)" "1.0.0"
assert_eq "坏版本后容器镜像 tag" "$(container_tag)" "1.0.0"
assert_eq "坏版本后容器健康" "$(health_status)" "healthy"
assert_eq "坏版本后数据库迁移头" "$(db_rev)" "0005_release_record"
assert_eq "坏版本后备份数（应 4，只增）" "$(backup_count)" "4"

say "=== 9) 备份只增且非空 ==="
BCOUNT="$(backup_count)"
BMIN="$(backup_min_size)"
[ "$BCOUNT" -ge 4 ] && pass "备份数 ≥ 4（实际 $BCOUNT）" || fail "备份数不足（$BCOUNT）"
[ "$BMIN" -gt 0 ] && pass "备份最小体积非空（${BMIN} 字节）" || fail "存在空备份"

say "=== 10) history 记录坏版本失败操作 ==="
HISTORY_OUT="$("${RELEASE[@]}" history)"
assert_contains "history 含 1.2.0-bad" "$HISTORY_OUT" "1.2.0-bad"
assert_contains "history 含 failed" "$HISTORY_OUT" "failed"

# ---- 清理（仅限 trans-e2e 命名空间）---------------------------------------
say "清理 trans-e2e 资源（不影响 trans-production）"
"${COMPOSE_BASE[@]}" down --remove-orphans >/dev/null 2>&1 || true
docker volume rm "$VOLUME" >/dev/null 2>&1 || true
for v in 1.0.0 1.1.0 1.2.0-bad; do docker rmi "$IMAGE:$v" >/dev/null 2>&1 || true; done
say "已清理。工作区保留在 $WORKSPACE（可人工复查 .releases/manifest.json、backups/）"

say "E2E 结果: 通过 ${PASSED} 项, 失败 ${FAILED} 项"
[ "$FAILED" -eq 0 ] && echo "== E2E 全部通过 ==" || echo "== E2E 存在失败 =="
exit "$FAILED"
