#!/usr/bin/env bash
# 需要用户介入时提醒：桌面通知 + 日志 + Thunderbird 邮件草稿。
#
# 用法:
#   scripts/notify.sh "标题" "正文"
#   NOTIFY_EMAIL=someone@example.com scripts/notify.sh "标题" "正文"
#
# 环境变量:
#   NOTIFY_EMAIL      收件人，默认取 Thunderbird 身份邮箱
#   NOTIFY_NO_MAIL=1  只发桌面通知，不开邮件窗口
#   NOTIFY_SMTP_HOST / NOTIFY_SMTP_PORT / NOTIFY_SMTP_USER / NOTIFY_SMTP_PASS
#                     配了就直接用 smtplib 发信（无需 Thunderbird）
#
# 说明：Thunderbird 账号是 Office365 + OAuth2，没有可复用的明文密码，
# 所以默认用 mailto: 唤起 Thunderbird 的撰写窗口（已登录，一键发送）。

set -uo pipefail

TITLE="${1:-latin_tool 通知}"
BODY="${2:-}"
EMAIL="${NOTIFY_EMAIL:-72655423@cityu-dg.edu.cn}"
LOG_FILE="${NOTIFY_LOG:-/tmp/opencode/notifications.log}"

mkdir -p "$(dirname "$LOG_FILE")"
echo "[$(date '+%F %T')] $TITLE | $BODY" >> "$LOG_FILE"

# 1) 桌面通知
if command -v notify-send >/dev/null 2>&1; then
    notify-send "latin_tool: $TITLE" "$BODY" 2>/dev/null || echo "notify-send 失败" >> "$LOG_FILE"
fi

# 2) 邮件
if [ "${NOTIFY_NO_MAIL:-0}" = "1" ]; then
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

title, body = sys.argv[1], sys.argv[2]
msg = EmailMessage()
msg["Subject"] = title
msg["From"] = os.environ["NOTIFY_SMTP_USER"]
msg["To"] = os.environ["NOTIFY_EMAIL"]
msg.set_content(body)

with smtplib.SMTP(os.environ["NOTIFY_SMTP_HOST"], int(os.environ.get("NOTIFY_SMTP_PORT", 587)), timeout=30) as s:
    s.starttls()
    s.login(os.environ["NOTIFY_SMTP_USER"], os.environ["NOTIFY_SMTP_PASS"])
    s.send_message(msg)
print("smtp sent")
PY
    exit 0
fi

# 没有 SMTP 配置 → 唤起 Thunderbird 撰写窗口（mailto）
SUBJECT_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$TITLE")
BODY_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$BODY")
MAILTO="mailto:${EMAIL}?subject=${SUBJECT_ENC}&body=${BODY_ENC}"

if command -v thunderbird >/dev/null 2>&1; then
    nohup thunderbird -compose "$MAILTO" >/dev/null 2>&1 &
elif command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "$MAILTO" >/dev/null 2>&1 &
else
    echo "无可用邮件客户端" >> "$LOG_FILE"
    exit 1
fi
echo "已唤起 Thunderbird 撰写窗口 -> $EMAIL" >> "$LOG_FILE"
