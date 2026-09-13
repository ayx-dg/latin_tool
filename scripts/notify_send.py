"""自动发送：唤起 Thunderbird 撰写窗口后按 Ctrl+Enter 发送。

用法:
    /tmp/opencode/uiauto/bin/python scripts/notify_send.py "标题" "正文"

说明: Thunderbird 账号是 Office365 + OAuth2，没有可复用的明文密码，
所以走「唤起已登录的 Thunderbird + 快捷键发送」这条路。
"""

import subprocess
import sys
import time
import urllib.parse

from pynput.keyboard import Controller, Key

EMAIL = "72655423@cityu-dg.edu.cn"
WAIT_SECONDS = 20


def main() -> int:
    title = sys.argv[1] if len(sys.argv) > 1 else "latin_tool 通知"
    body = sys.argv[2] if len(sys.argv) > 2 else ""
    auto = "--no-send" not in sys.argv

    mailto = (
        f"mailto:{EMAIL}"
        f"?subject={urllib.parse.quote(title)}"
        f"&body={urllib.parse.quote(body)}"
    )
    print("唤起 Thunderbird:", mailto[:80], "...")
    subprocess.Popen(
        ["thunderbird", "-compose", mailto],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not auto:
        print("仅打开窗口，不自动发送")
        return 0

    print(f"等待 {WAIT_SECONDS} 秒让撰写窗口获得焦点 …")
    time.sleep(WAIT_SECONDS)

    keyboard = Controller()
    with keyboard.pressed(Key.ctrl):
        keyboard.press(Key.enter)
        keyboard.release(Key.enter)
    print("已按下 Ctrl+Enter（Thunderbird 应立即发送）")
    time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
