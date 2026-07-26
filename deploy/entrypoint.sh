#!/bin/sh
set -eu

fail() {
    printf '%s\n' "trans-entrypoint: $*" >&2
    exit 1
}

validate_secret_file() {
    variable_name=$1
    secret_path=$(printenv "$variable_name" 2>/dev/null || true)

    [ -n "$secret_path" ] || fail "$variable_name must name a Docker secret file"
    [ -f "$secret_path" ] || fail "$variable_name does not reference a regular file: $secret_path"
    [ ! -L "$secret_path" ] || fail "$variable_name must not reference a symbolic link: $secret_path"
    [ -r "$secret_path" ] || fail "$variable_name is not readable by the application user: $secret_path"
    [ -s "$secret_path" ] || fail "$variable_name references an empty secret: $secret_path"

    mode=$(stat -c '%a' "$secret_path") || fail "cannot inspect permissions for $secret_path"
    owner_permissions=$((mode / 100 % 10))
    group_permissions=$((mode / 10 % 10))
    other_permissions=$((mode % 10))
    [ $((owner_permissions & 1)) -eq 0 ] \
        && [ $((group_permissions & 3)) -eq 0 ] \
        && [ $((other_permissions & 2)) -eq 0 ] \
        || fail "$secret_path must not be writable outside its owner or executable (mode $mode)"
}

validate_secret_file TRANS_ADMIN_BOOTSTRAP_PASSWORD_FILE
validate_secret_file TRANS_MASTER_KEY_FILE

state_dir=${TRANS_STATE_DIR:-/var/lib/trans}
data_dir=${TRANS_DATA_DIR:-$state_dir}
upload_dir=${TRANS_UPLOAD_DIR:-$state_dir/uploads}
export_dir=${TRANS_EXPORT_DIR:-$state_dir/exports}
temp_dir=${TRANS_TEMP_DIR:-$state_dir/tmp}
backup_dir=${TRANS_BACKUP_DIR:-$state_dir/backups}

for directory in "$state_dir" "$data_dir" "$upload_dir" "$export_dir" "$temp_dir" "$backup_dir"; do
    mkdir -p "$directory" || fail "cannot create runtime directory: $directory"
    [ -d "$directory" ] || fail "runtime path is not a directory: $directory"
    [ -w "$directory" ] || fail "runtime directory is not writable: $directory"
done

cd /app
python -m alembic -c /app/backend/alembic.ini upgrade head

exec python -m uvicorn backend.app.main:app \
    --host "${TRANS_HOST:-0.0.0.0}" \
    --port "${TRANS_PORT:-8000}" \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips "${TRANS_FORWARDED_ALLOW_IPS:-127.0.0.1}"
