#!/usr/bin/env python3
"""Claude / Gemini(Antigravity) の使用量コレクタ（標準ライブラリのみ・Python 3.9+）。

Mac 上で定期実行し、正規化した usage.json を書き出す。
  - Mac メニューバー(SwiftBar) と iPhone/Mac の Scriptable ウィジェットがこれを読む。

取得元（どちらも公式ドキュメントのない非公式経路。仕様変更で壊れうる）:
  - Claude : GET https://api.anthropic.com/api/oauth/usage
             (Claude Code の OAuth トークン / header anthropic-beta: oauth-2025-04-20)
  - Gemini : `agy -p /usage --output-format json`  (Antigravity CLI >= 1.1.11)

使い方:
  ai_usage.py collect            # 取得して usage.json を更新（既定）
  ai_usage.py show               # usage.json を表で表示
  ai_usage.py raw claude|gemini  # 生レスポンスを表示（パーサ調整用）
  ai_usage.py diagnose           # Claude 認証情報の構造のみ表示（トークン値は出さない）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
LOCAL_DIR = Path.home() / ".ai-usage"
ICLOUD_DIR = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "AIUsage"
# Scriptable 専用の iCloud フォルダ（iPhone の「ファイル」アプリの iCloud Drive/Scriptable）。
# ウィジェットはここを直接読めるので、ファイルブックマークの登録が不要になる。
SCRIPTABLE_DIR = Path.home() / "Library" / "Mobile Documents" / "iCloud~dk~simonbs~Scriptable" / "Documents"
FILENAME = "usage.json"

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
MAX_AGY_OUTPUT = 1024 * 1024  # 1 MiB


class ProviderError(Exception):
    """ユーザーに見せてよい、取得失敗の理由。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #
def token_from_credentials(obj: dict) -> str:
    try:
        token = obj["claudeAiOauth"]["accessToken"]
    except (KeyError, TypeError):
        raise ProviderError("Claude Code の認証情報に accessToken が見つかりません") from None
    if not token:
        raise ProviderError("Claude Code の accessToken が空です")
    return token


def read_claude_token() -> str:
    env = os.environ.get("CLAUDE_OAUTH_TOKEN")
    if env:
        return env
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
            )
            if r.returncode == 0 and r.stdout.strip():
                return token_from_credentials(json.loads(r.stdout))
        except (OSError, subprocess.SubprocessError, ValueError, ProviderError):
            pass  # Keychain が空/別形式でもファイル側を試す
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    path = cfg / ".credentials.json"
    try:
        return token_from_credentials(json.loads(path.read_text()))
    except (OSError, ValueError, ProviderError):
        raise ProviderError(
            "Claude の accessToken を取得できません（Keychain/ファイルとも空か未存在）。"
            "`ai_usage.py diagnose` の出力を確認してください"
        ) from None


def _mask(v):
    """値は出さず、型と長さだけ返す（診断用）。"""
    if isinstance(v, dict):
        return {k: _mask(x) for k, x in v.items()}
    if isinstance(v, str):
        return f"<str len={len(v)}>"
    if isinstance(v, (bool, int, float)):
        return v  # expiresAt などの数値・真偽値は秘密ではない
    if isinstance(v, list):
        return f"<list len={len(v)}>"
    return f"<{type(v).__name__}>"


def diagnose_claude() -> dict:
    """認証情報の「構造」だけを出す。トークン等の値は一切出さない。"""
    info = {"env_CLAUDE_OAUTH_TOKEN": bool(os.environ.get("CLAUDE_OAUTH_TOKEN"))}
    if sys.platform == "darwin":
        try:
            meta = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
                                  capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            # 属性行のみ（-w を付けないのでパスワードは含まれない）
            info["keychain_attrs"] = [l.strip() for l in meta.stdout.splitlines()
                                      if l.strip().startswith(('"acct"', '"svce"', "keychain:"))]
            info["keychain_found"] = meta.returncode == 0
            pw = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                                capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            if pw.returncode == 0:
                try:
                    info["keychain_structure"] = _mask(json.loads(pw.stdout))
                except ValueError:
                    info["keychain_structure"] = f"<non-json len={len(pw.stdout.strip())}>"
        except (OSError, subprocess.SubprocessError) as e:
            info["keychain_error"] = str(e)
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
    f = cfg / ".credentials.json"
    info["credentials_file_exists"] = f.exists()
    if f.exists():
        try:
            info["credentials_file_structure"] = _mask(json.loads(f.read_text()))
        except (OSError, ValueError):
            info["credentials_file_structure"] = "<unreadable>"
    return info


def fetch_claude_usage(token: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        CLAUDE_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
            "User-Agent": "ai-usage-widget/1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            # 自前でリフレッシュはしない（Claude Code 側のログインを壊さないため）
            raise ProviderError(
                "Claude のトークンが無効/期限切れです。Claude Code を一度起動してください"
            ) from None
        if e.code == 429:
            raise ProviderError("Claude usage API がレート制限中です（間隔を空けてください）") from None
        raise ProviderError(f"Claude usage API が HTTP {e.code} を返しました") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise ProviderError(f"Claude usage API に接続できません: {e}") from None


_CLAUDE_LABELS = {"five_hour": ("5時間", "five_hour"), "seven_day": ("週間", "weekly")}
# レート制限の枠ではない項目。iguana_necktie はプロモーションのクラウドセッション・クレジット
# （CodexBar docs/claude.md）。omelette_promotional も同系統の内部/プロモ用フィールド。
_CLAUDE_NON_WINDOW_KEYS = {"iguana_necktie", "omelette_promotional", "extra_usage"}


def _claude_label(key: str):
    if key in _CLAUDE_LABELS:
        return _CLAUDE_LABELS[key]
    if key.startswith("seven_day_"):  # モデル別の週間枠（例: seven_day_opus）
        return "週間 " + key[len("seven_day_"):].replace("_", " ").title(), "other"
    return key, "other"


def parse_claude(resp: dict) -> list:
    """{five_hour:{utilization,resets_at}, seven_day:{...}, ...} -> windows。
    utilization は使用率 % として扱う。"""
    windows = []
    if not isinstance(resp, dict):
        raise ProviderError("Claude usage の応答形式が想定外です")
    for key, val in resp.items():
        if key in _CLAUDE_NON_WINDOW_KEYS:
            continue
        if not isinstance(val, dict) or val.get("utilization") is None:
            continue
        try:
            used = float(val["utilization"])
        except (TypeError, ValueError):
            continue
        label, kind = _claude_label(key)
        windows.append({
            "id": key, "label": label, "kind": kind, "group": "Claude",
            "used_percent": round(used, 1),
            "remaining_percent": round(max(0.0, 100.0 - used), 1),
            "resets_at": val.get("resets_at"), "reset_text": None,
        })
    if not windows:
        raise ProviderError("Claude usage の応答に使用率が含まれていません")
    order = {"five_hour": 0, "weekly": 1}
    windows.sort(key=lambda w: order.get(w["kind"], 9))
    return windows


def collect_claude() -> list:
    return parse_claude(fetch_claude_usage(read_claude_token()))


# --------------------------------------------------------------------------- #
# Gemini (Antigravity CLI)
# --------------------------------------------------------------------------- #
def run_agy_usage(timeout: int = 90) -> dict:
    exe = os.environ.get("ANTIGRAVITY_CLI_PATH") or shutil.which("agy")
    if not exe:
        raise ProviderError("agy が見つかりません（brew install --cask antigravity-cli → agy で一度ログイン）")
    env = dict(os.environ, AGY_CLI_DISABLE_AUTO_UPDATE="true")  # 小文字 true が必須（CodexBar docs）
    try:
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [exe, "-p", "/usage", "--output-format", "json"],
                cwd=tmp, env=env, capture_output=True, text=True,
                timeout=timeout, stdin=subprocess.DEVNULL,
            )
    except subprocess.TimeoutExpired:
        raise ProviderError("agy /usage がタイムアウトしました（agy でログイン済みか確認）") from None
    except OSError as e:
        raise ProviderError(f"agy を実行できません: {e}") from None
    out = r.stdout
    if len(out) > MAX_AGY_OUTPUT:
        raise ProviderError("agy の出力が大きすぎます")
    if r.returncode != 0 and not out.strip():
        raise ProviderError("agy /usage が失敗しました（agy でログインし直してください）")
    return parse_json_loose(out)


def parse_json_loose(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(text[a : b + 1])
        except ValueError:
            pass
    raise ProviderError("agy の出力を JSON として解釈できません（`ai_usage.py raw gemini` で確認）")


def _cadence(*texts) -> str:
    s = " ".join(t for t in texts if isinstance(t, str)).lower()
    if "week" in s:
        return "weekly"
    if "hour" in s or "session" in s or "five" in s or "5h" in s:
        return "five_hour"
    return "other"


def _short_group(name: str) -> str:
    low = name.lower().strip()
    if "claude" in low or "gpt" in low:
        return "Claude+GPT"
    if low in ("gemini", "gemini models"):
        return "Gemini"
    return name  # 例: "Gemini 4 Argon" は別枠のまま残す


_KIND_LABEL = {"five_hour": "5時間", "weekly": "週間", "other": ""}


def parse_agy_usage(data) -> list:
    """`command.data.groups[].buckets[]` を再帰的に探して windows 化。

    実機出力（agy -p /usage --output-format json）で確認した形:
      bucket = {id, name, window: "weekly"|"5h", remaining_fraction: 0-1, reset_time: ISO, description?}
    CodexBar docs の別形式（remaining.remainingFraction / resetTime）も受け付ける。"""
    windows, seen = [], set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("buckets"), list):
                group_name = str(node.get("name") or node.get("displayName") or "")
                for b in node["buckets"]:
                    add_bucket(group_name, b)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    def add_bucket(group_name, b):
        if not isinstance(b, dict):
            return
        rem = b.get("remaining") if isinstance(b.get("remaining"), dict) else b
        frac = rem.get("remaining_fraction")
        if frac is None:
            frac = rem.get("remainingFraction")
        if isinstance(frac, bool) or not isinstance(frac, (int, float)):
            return  # 使用量不明のバケットは捏造せず捨てる
        frac = min(1.0, max(0.0, float(frac)))
        bid = b.get("id") or b.get("bucketId")
        bname = b.get("name") or b.get("displayName")
        kind = _cadence(b.get("window"), bid, bname)
        group = _short_group(group_name) if group_name else "Gemini"
        key = (group_name, kind, bid)
        if key in seen:
            return
        seen.add(key)
        desc = b.get("description")
        slug = "".join(c if c.isalnum() else "_" for c in group.lower())
        windows.append({
            "id": f"{slug}_{kind}",
            "label": _KIND_LABEL[kind] or str(bname or bid or "?"),
            "kind": kind, "group": group,
            "used_percent": round((1.0 - frac) * 100, 1),
            "remaining_percent": round(frac * 100, 1),
            "resets_at": (b.get("reset_time") or b.get("resetTime") or b.get("resets_at")
                          or rem.get("reset_time") or rem.get("resetTime")),
            "reset_text": desc if isinstance(desc, str) else None,
        })

    walk(data)
    if not windows:
        raise ProviderError("agy /usage に使用量バケットがありません（`ai_usage.py raw gemini` で確認）")
    gorder = {"Gemini": 0, "Claude+GPT": 1}
    korder = {"five_hour": 0, "weekly": 1}
    windows.sort(key=lambda w: (gorder.get(w["group"], 9), korder.get(w["kind"], 9)))
    return windows


def collect_gemini() -> list:
    return parse_agy_usage(run_agy_usage())


# --------------------------------------------------------------------------- #
# 集約・書き出し
# --------------------------------------------------------------------------- #
PROVIDERS = {"claude": collect_claude, "gemini": collect_gemini}


def load_previous(path: Path) -> dict:
    try:
        return json.loads(path.read_text()).get("providers", {})
    except (OSError, ValueError, AttributeError):
        return {}


def collect_all(names, previous: dict) -> dict:
    result = {}
    for name in names:
        try:
            windows = PROVIDERS[name]()
            result[name] = {"ok": True, "stale": False, "fetched_at": now_iso(),
                            "error": None, "windows": windows}
        except ProviderError as e:
            prev = previous.get(name) or {}
            result[name] = {  # 失敗時は最後の成功値を stale として残す
                "ok": False, "stale": bool(prev.get("windows")),
                "fetched_at": prev.get("fetched_at"), "error": str(e),
                "windows": prev.get("windows", []),
            }
    return result


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    os.replace(tmp, path)


def default_outputs() -> list:
    outs = [LOCAL_DIR / FILENAME]
    if ICLOUD_DIR.parent.exists():  # iCloud Drive が有効な Mac のみ
        outs.append(ICLOUD_DIR / FILENAME)
    if SCRIPTABLE_DIR.is_dir():  # Scriptable の iCloud フォルダが Mac に見えている場合のみ
        outs.append(SCRIPTABLE_DIR / FILENAME)
    return outs


def cmd_collect(args) -> int:
    outs = [Path(p) for p in args.out] if args.out else default_outputs()
    names = [n.strip() for n in args.only.split(",") if n.strip()]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        print(f"未対応のプロバイダ: {unknown}", file=sys.stderr)
        return 2
    providers = collect_all(names, load_previous(outs[0]))
    payload = {"version": SCHEMA_VERSION, "generated_at": now_iso(), "providers": providers}
    for p in outs:
        write_atomic(p, payload)
    for n, pr in providers.items():
        if not pr["ok"]:
            print(f"[{n}] {pr['error']}", file=sys.stderr)
    print("wrote: " + ", ".join(str(p) for p in outs))
    return 0 if any(p["ok"] for p in providers.values()) else 1


def cmd_show(args) -> int:
    path = Path(args.file) if args.file else LOCAL_DIR / FILENAME
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        print(f"{path} を読めません: {e}", file=sys.stderr)
        return 1
    print(f"generated_at: {data.get('generated_at')}")
    for name, pr in data.get("providers", {}).items():
        flag = "" if pr.get("ok") else f"  !! {pr.get('error')}" + ("（前回値）" if pr.get("stale") else "")
        print(f"\n{name}{flag}")
        for w in pr.get("windows", []):
            print(f"  {w['group']:<11} {w['label']:<6} 残り {w['remaining_percent']:>5.1f}%  "
                  f"reset={w.get('resets_at') or w.get('reset_text') or '-'}")
    return 0


def cmd_raw(args) -> int:
    try:
        raw = fetch_claude_usage(read_claude_token()) if args.provider == "claude" else run_agy_usage()
    except ProviderError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(raw, ensure_ascii=False, indent=2))
    return 0


def cmd_diagnose(args) -> int:
    print(json.dumps(diagnose_claude(), ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("collect", help="取得して usage.json を更新")
    c.add_argument("--out", action="append", help="出力先（複数可）。既定: ~/.ai-usage と iCloud Drive/AIUsage")
    c.add_argument("--only", default="claude,gemini", help="カンマ区切り（既定 claude,gemini）")
    s = sub.add_parser("show", help="usage.json を表示")
    s.add_argument("--file")
    r = sub.add_parser("raw", help="生レスポンスを表示")
    r.add_argument("provider", choices=sorted(PROVIDERS))
    sub.add_parser("diagnose", help="Claude 認証情報の構造を表示（値は出さない）")
    args = ap.parse_args(argv)
    if args.cmd in (None, "collect"):
        if args.cmd is None:
            args = ap.parse_args(["collect"])
        return cmd_collect(args)
    return {"show": cmd_show, "raw": cmd_raw, "diagnose": cmd_diagnose}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
