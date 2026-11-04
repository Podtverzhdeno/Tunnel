"""One explicitly requested new ChatGPT chat, using the user-installed one-shot extension.
This invokes the default Windows browser; no private API or browser credentials.
"""
import json
import os
import platform

if __name__ == "__main__":
    if platform.system() != "Windows":
        raise SystemExit("Windows default-browser launch required")
    url = "https://chatgpt.com/#chat-launch-smoke=v1"
    os.startfile(url)
    print(json.dumps({
        "os_launch": "requested",
        "url": url,
        "script_attempted_send": False,
        "extension_expected_to_send_one_message": True,
        "browser_chat_verified": False
    }, ensure_ascii=False))
