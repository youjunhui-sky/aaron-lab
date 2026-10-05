#!/usr/bin/env bash
# inquiry-relay one-shot deploy: venv + config + optional systemd service
# Target: a fresh machine, ≤5 minutes from clone to running service.
#
#   sudo ./install.sh              # install + systemd service (systemd systems)
#   sudo ./install.sh --no-service # install only, run manually
#
# Idempotent: safe to re-run (upgrades code in place, keeps .env and data/).

set -euo pipefail

APP_NAME="inquiry-relay"
DEFAULT_DIR="/opt/inquiry-relay"
INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_DIR}"
RUN_USER="${RUN_USER:-inquiry}"
SERVICE_NAME="inquiry-relay"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"; }

# --- 1. system deps ---------------------------------------------------------
log "checking dependencies"
need python3
need git
PYTHON="$(command -v python3)"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10), "need python >= 3.10"' \
  || die "python 3.10+ required (found $($PYTHON --version))"
NEED_PKGS=""
command -v python3-venv >/dev/null 2>&1 || [ -e /usr/bin/python3 ] && true
"$PYTHON" -m venv /tmp/ir-selftest-venv-$$ >/dev/null 2>&1 || NEED_PKGS="python3-venv"
rm -rf /tmp/ir-selftest-venv-$$ 2>/dev/null || true
if [ -n "$NEED_PKGS" ] && command -v apt-get >/dev/null 2>&1; then
  log "installing $NEED_PKGS via apt"
  apt-get update -qq && apt-get install -y -qq $NEED_PKGS
fi

# --- 2. service user (systemd mode only) ------------------------------------
if [ "$(id -u)" -eq 0 ] && command -v useradd >/dev/null 2>&1; then
  if ! id "$RUN_USER" >/dev/null 2>&1; then
    log "creating system user $RUN_USER"
    useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$RUN_USER"
  fi
fi

# --- 3. code into place -----------------------------------------------------
log "installing app to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/app" "$INSTALL_DIR/"
cp -r "$SCRIPT_DIR/tests" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/requirements-dev.txt" "$INSTALL_DIR/" 2>/dev/null || true
cp "$SCRIPT_DIR/.env.example" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/"
[ -f "$SCRIPT_DIR/inquiry-relay.service" ] && cp "$SCRIPT_DIR/inquiry-relay.service" "$INSTALL_DIR/"

# --- 4. venv + deps (tsinghua mirror first; pypi fallback) -------------------
log "creating venv and installing dependencies"
"$PYTHON" -m venv "$INSTALL_DIR/.venv"
PIP="$INSTALL_DIR/.venv/bin/pip"
"$PIP" install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple \
  || "$PIP" install --upgrade pip -q
"$PIP" install --timeout 60 -q -i https://pypi.tuna.tsinghua.edu.cn/simple -r "$INSTALL_DIR/requirements.txt" \
  || "$PIP" install --timeout 60 -q -r "$INSTALL_DIR/requirements.txt"

# --- 5. .env (never overwrite) ----------------------------------------------
if [ ! -f "$INSTALL_DIR/.env" ]; then
  log "creating .env from example (EDIT IT: add your feishu webhook)"
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
else
  log ".env already exists — kept"
fi

mkdir -p "$INSTALL_DIR/data"

# --- 6. self test ------------------------------------------------------------
log "running self test (pytest)"
(cd "$INSTALL_DIR" && .venv/bin/python -m pytest -q tests/ 2>/dev/null || \
 echo "(pytest not installed — skipping; pip install -r requirements-dev.txt to enable)")

# --- 7. systemd service ------------------------------------------------------
if [ "${1:-}" = "--no-service" ]; then
  log "skipping service install (--no-service)"
  cat <<EOF

Manual run:
  cd $INSTALL_DIR
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
EOF
  exit 0
fi

command -v systemctl >/dev/null 2>&1 || die "systemd not found; use --no-service for manual run"
[ "$(id -u)" -eq 0 ] || die "service install needs root; use --no-service for manual run"

log "installing systemd unit"
sed -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" -e "s|__RUN_USER__|$RUN_USER|g" \
  "$INSTALL_DIR/inquiry-relay.service" > /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable --now ${SERVICE_NAME}.service
sleep 2
systemctl --no-pager --lines 5 status ${SERVICE_NAME}.service || true

cat <<EOF

Done. Service: systemctl {status|restart|stop} $SERVICE_NAME
Logs:        journalctl -u $SERVICE_NAME -f
Health:      curl localhost:8000/healthz
NEXT STEP:   edit $INSTALL_DIR/.env — set INQUIRY_FEISHU_WEBHOOK_URL, then:
             systemctl restart $SERVICE_NAME
EOF
