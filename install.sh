#!/bin/bash
# Mac 用インストーラ: collector を launchd で5分ごとに実行し、SwiftBar プラグインをリンクする。
# 何度実行しても安全（再インストール扱い）。 アンインストール: ./install.sh --uninstall
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "このスクリプトは macOS 用です" >&2; exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.ai-usage.collector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || echo /nonexistent)/ai-usage.5m.py"
  echo "削除しました（~/.ai-usage と iCloud Drive/AIUsage のデータは残しています）"; exit 0
fi

PYTHON="$(command -v python3 || true)"
[[ -n "$PYTHON" ]] || { echo "python3 が見つかりません（xcode-select --install）" >&2; exit 1; }

mkdir -p "$HOME/.ai-usage" "$HOME/Library/LaunchAgents"
[[ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]] \
  && mkdir -p "$HOME/Library/Mobile Documents/com~apple~CloudDocs/AIUsage" \
  || echo "注意: iCloud Drive が無効のようです。iPhone ウィジェットには iCloud Drive が必要です。"

sed -e "s|__PYTHON__|$PYTHON|g" -e "s|__COLLECTOR__|$ROOT/collector/ai_usage.py|g" -e "s|__HOME__|$HOME|g" \
  "$ROOT/launchd/com.ai-usage.collector.plist.template" > "$PLIST"

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "launchd 登録: ${PLIST}（5分ごと）"

PLUGIN_DIR="$(defaults read com.ameba.SwiftBar PluginDirectory 2>/dev/null || true)"
if [[ -n "$PLUGIN_DIR" && -d "$PLUGIN_DIR" ]]; then
  chmod +x "$ROOT/mac/ai-usage.5m.py"
  ln -sf "$ROOT/mac/ai-usage.5m.py" "$PLUGIN_DIR/ai-usage.5m.py"
  echo "SwiftBar プラグインをリンク: $PLUGIN_DIR/ai-usage.5m.py"
else
  echo "SwiftBar が未設定です。 brew install --cask swiftbar → 起動してプラグインフォルダを選び、再度このスクリプトを実行してください。"
fi

echo
echo "初回の動作確認（ここで Keychain の許可ダイアログが出たら「常に許可」）:"
echo "  $PYTHON $ROOT/collector/ai_usage.py collect && $PYTHON $ROOT/collector/ai_usage.py show"
