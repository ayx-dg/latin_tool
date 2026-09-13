#!/usr/bin/env bash
# 需要用户介入时的提醒通道。
#
# 用法:
#   scripts/notify.sh "标题" "正文"            # 默认：ntfy + 桌面通知
#   NOTIFY_MAIL=1 scripts/notify.sh "标题" "正文"   # 额外唤起 Thunderbird 撰写窗口
#
# 通道优先级:
#   1. ntfy    —— 手机推送（离开终端时最可靠）
#   2. 桌面通知 —— notify-send（人在电脑前时）
#   3. 邮件    —— Thunderbird（需 NOTIFY_MAIL=1；或配 NOTIFY_SMTP_* 直接 smtplib 发送）
#
# 环境变量:
#   NTFY_URL     默认 http://100.112.38.87:8080/dev
#   NTFY_TAGS    默认 warning（可选 white_check_mark / x / hourglass）
#   NTFY_PRIO    默认留空，阻塞等待时设 5
#   NOTIFY_EMAIL 收件人，默认 Thunderbird 身份邮箱

set -uo pipefail

TITLE="${1:-latin_tool 通知}"
BODY="${2:-}"
NTFY_URL="${NTFY_URL:-http://100.112.38.87:8080/dev}"
TAGS="${NTFY_TAGS:-warning}"
EMAIL="${NOTIFY_EMAIL:-72655423@cityu-dg.edu.cn}"
LOG_FILE="${NOTIFY_LOG:-/tmp/opencode/notifications.log}"

mkdir -p "$(dirname "$LOG_FILE")"
echo "[$(date '+%F %T')] [$TAGS] $TITLE | $BODY" >> "$LOG_FILE"

# 1) ntfy 手机推送
prio_header=()
[ -n "${NTFY_PRIO:-}" ] && prio_header=(-H "Priority: ${NTFY_PRIO}")
if curl -fsS --max-time 10 \
    -H "Title: latin_tool: ${TITLE}" \
    -H "Tags: ${TAGS}" \
    "${prio_header[@]}" \
    -d "$BODY" "$NTFY_URL" >/dev/null 2>&1; then
    echo "ntfy 已推送" >> "$LOG_FILE"
else
    echo "ntfy 推送失败（服务是否在线？systemctl --user restart ntfy）" >> "$LOG_FILE"
fi

# 2) 桌面通知
if command -v notify-send >/dev/null 2>&1; then
    notify-send "latin_tool: $TITLE" "$BODY" 2>/dev/null || true
fi

# 3) 邮件（可选）
if [ "${NOTIFY_MAIL:-0}" != "1" ]; then
    exit 0
fi

if [ -n "${NOTIFY_SMTP_HOST:-}" ] && [ -n "${NOTIFY_SMTP_USER:-}" ] && [ -n "${NOTIFY_SMTP_PASS:-}" ]; then
    NOTIFY_SMTP_HOST="$NOTIFY_SMTP_HOST" \
    NOTIFY_SMTP_PORT="${NOTIFY_SMTP_PORT:-587}" \
    NOTIFY_SMTP_USER="$NOTIFY_SMTP_USER" \
    NOTIFY_SMTP_PASS="$NOTIFY_SMTP_PASS" \
    NOTIFY_EMAIL="$EMAIL" \
    python3 - "$TITLE" "$BODY" <<'PY' || echo "SMTP 发送失败" >> "$LOG_FILE"
import os, smtplib, sys
from email.message import EmailMessage

msg = EmailMessage()
msg["Subject"] = sys.argv[1]
msg["From"] = os.environ["NOTIFY_SMTP_USER"]
msg["To"] = os.environ["NOTIFY_EMAIL"]
msg.set_content(sys.argv[2])
with smtplib.SMTP(os.environ["NOTIFY_SMTP_HOST"], int(os.environ.get("NOTIFY_SMTP_PORT", 587)), timeout=30) as s:
    s.starttls()
    s.login(os.environ["NOTIFY_SMTP_USER"], os.environ["NOTIFY_SMTP_PASS"])
    s.send_message(msg)
PY
    exit 0
fi

SUBJECT_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$TITLE")
BODY_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$BODY")
nohup thunderbird -compose "mailto:${EMAIL}?subject=${SUBJECT_ENC}&body=${BODY_ENC}" >/dev/null 2>&1 &
echo "已唤起 Thunderbird 撰写窗口 -> $EMAIL" >> "$LOG_FILE"
