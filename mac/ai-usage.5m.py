#!/usr/bin/env python3
# <xbar.title>AI Usage (Claude / Gemini)</xbar.title>
# <xbar.desc>Claude と Gemini(Antigravity) の残り使用量をメニューバーに表示</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
#
# SwiftBar のプラグインフォルダに置く（install.sh がシンボリックリンクを作る）。
# ファイル名の「5m」が更新間隔。表示するだけで、取得は launchd の ai_usage.py が行う。
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("AI_USAGE_FILE", Path.home() / ".ai-usage" / "usage.json"))
COLLECTOR = Path(os.environ.get("AI_USAGE_COLLECTOR", Path(__file__).resolve().parent.parent / "collector" / "ai_usage.py"))
STALE_AFTER_MIN = 30
SHORT = {"claude": "C", "gemini": "G"}


def color(remaining):
    return "#FF3B30" if remaining <= 10 else "#FF9500" if remaining <= 30 else "#34C759"


def parse_dt(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def countdown(w):
    dt = parse_dt(w.get("resets_at"))
    if dt is None:
        return w.get("reset_text") or ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    sec = int((dt - datetime.now(timezone.utc)).total_seconds())
    if sec <= 0:
        return "リセット済み"
    d, rem = divmod(sec, 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}日{h}時間" if d else f"{h}時間{m}分"


def primary(pr):
    ws = [w for w in pr.get("windows", []) if w.get("kind") == "five_hour"] or pr.get("windows", [])
    return min((w["remaining_percent"] for w in ws), default=None)


def main():
    try:
        data = json.loads(DATA.read_text())
    except (OSError, ValueError):
        print("AI ?")
        print("---")
        print(f"データがありません: {DATA}")
        print(f"今すぐ取得 | bash={sys.executable} param1={COLLECTOR} param2=collect terminal=false refresh=true")
        return

    gen = parse_dt(data.get("generated_at"))
    old = gen is None or (datetime.now(timezone.utc) - gen.astimezone(timezone.utc)).total_seconds() > STALE_AFTER_MIN * 60

    parts = []
    for name, pr in data.get("providers", {}).items():
        p = primary(pr)
        parts.append(f"{SHORT.get(name, name[:1].upper())} --" if p is None else f"{SHORT.get(name, name[:1].upper())} {p:.0f}%")
    print(" · ".join(parts) + (" ⚠︎" if old else ""))
    print("---")
    for name, pr in data.get("providers", {}).items():
        print(f"{name.capitalize()} | size=13")
        if not pr.get("ok"):
            print(f"--⚠︎ {pr.get('error')}{'（前回値）' if pr.get('stale') else ''} | color=#FF9500")
        last = None
        for w in pr.get("windows", []):
            if w.get("group") != last and len({x.get("group") for x in pr["windows"]}) > 1:
                print(f"--{w.get('group')}")
            last = w.get("group")
            r = w["remaining_percent"]
            print(f"{w['label']}  残り {r:.0f}%  ⟳ {countdown(w)} | color={color(r)} font=Menlo")
        print("---")
    print(f"取得: {data.get('generated_at', '?')}" + ("  ⚠︎ 古い" if old else ""))
    print(f"今すぐ更新 | bash={sys.executable} param1={COLLECTOR} param2=collect terminal=false refresh=true")


if __name__ == "__main__":
    main()
