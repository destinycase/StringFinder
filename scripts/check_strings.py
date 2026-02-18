import sys
import os
import re

# src 경로 추가
sys.path.append(os.path.abspath("src"))
from utils.app_strings import AppStrings


def get_defined_strings():
    return {attr for attr in dir(AppStrings) if not attr.startswith("__")}


def get_used_strings(src_dir):
    used = set()
    pattern = re.compile(r"AppStrings\.([a-zA-Z0-9_]+)")
    for root, dirs, files in os.walk(src_dir):
        for file in files:
            if file.endswith(".py"):
                with open(os.path.join(root, file), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                    matches = pattern.findall(content)
                    for m in matches:
                        used.add(m)
    return used


if __name__ == "__main__":
    defined = get_defined_strings()
    used = get_used_strings("src")

    missing = used - defined
    if missing:
        print(f"Missing attributes in AppStrings: {missing}")
    else:
        print("All AppStrings references are valid.")

    unused = defined - used
    if unused:
        print(f"Unused attributes in AppStrings: {unused}")

    # Check for LOG_ prefix consistency
    logs_without_vessel = [
        u
        for u in used
        if u.startswith("LOG_")
        and not u.startswith(("LOG_SYS_", "LOG_SCH_", "LOG_WKR_", "LOG_RES_", "LOG_CFG_", "LOG_SES_", "LOG_PRF_"))
    ]
    if logs_without_vessel:
        print(f"Logs without proper prefix: {logs_without_vessel}")
