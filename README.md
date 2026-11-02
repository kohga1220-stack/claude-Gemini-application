# AI Usage — Claude / Gemini の残り使用量を iPhone と Mac で見る

Device Board のウィジェットのように、**Claude と Gemini の「5時間枠」「週間枠」の残り%** と
リセットまでの時間を、iPhone のホーム画面と Mac のメニューバーに表示します。

```
┌─ Mac ───────────────────────────────────────────┐
│ launchd(5分毎) → collector/ai_usage.py          │
│   ├ Claude : api.anthropic.com/api/oauth/usage │
│   └ Gemini : agy -p /usage --output-format json│
│        ↓ usage.json（正規化済み）               │
│   ├ ~/.ai-usage/usage.json ─→ SwiftBar(メニューバー)
│   ├ iCloud Drive/AIUsage/usage.json            │
│   └ iCloud Drive/Scriptable/usage.json ─┐       │
└──────────────────────────────────────┼──────────┘
                                       ↓ iCloud 同期
              iPhone / Mac の Scriptable ウィジェット (iphone/AIUsage.js)
```

## 先に知っておくこと（正直な現状）

| 項目 | 状態 |
|---|---|
| Claude の取得元 | **非公式**だが**実機で動作確認済み**。Claude Code の OAuth トークンで usage エンドポイントを叩く。`five_hour` / `seven_day` の使用率・リセット時刻が claude.ai の「ご利用状況」画面の表示と一致することを確認（2026-11-03、1回分の照合）。`iguana_necktie` などのプロモーション用フィールドは枠として表示しない |
| Gemini の取得元 | **非公式**だが**実機で動作確認済み**。5時間・週間枠は **Antigravity（`agy` CLI）側** の仕様（対象が Antigravity の枠であることは本人に確認済み）。`agy -p /usage --output-format json` の実出力（`command.data.groups[].buckets[]`、`remaining_fraction` / `reset_time`）に合わせたパーサ。Gemini CLI の個人向け OAuth は 2026-06-18 に終了済み（CodexBar の docs による） |
| テスト | 単体テスト 17 件（Gemini の入力は実機出力そのもの、Claude の入力は合成データ）。Scriptable は **iPhone 実機で Scriptable 内のプレビュー表示を確認済み**（Claude/Gemini の5時間・週間、リセットまでの時間）。ホーム画面のウィジェットとしての更新頻度は iOS 任せで未検証 |
| 対象外 | Gemini アプリ（チャット画面）自体の残量。取得手段が見つかっていない |
| 更新される条件 | **Mac が起動している間だけ**。Mac がスリープ中は iPhone の表示も古くなる（古いと「⚠︎ 古いデータ」表示） |

非公式 API は予告なく変わります。壊れたら `ai_usage.py raw <provider>` の出力を見てパーサを直してください。

## セットアップ

### 1. Mac

```bash
# 前提: Claude Code にログイン済み / Antigravity CLI を入れて一度ログイン
brew install --cask antigravity-cli && agy      # Gemini 側（ログインして終了）
brew install --cask swiftbar                    # メニューバー表示用（起動してプラグインフォルダを選ぶ）

./install.sh                                    # launchd 登録 + SwiftBar リンク
python3 collector/ai_usage.py collect           # 初回実行（Keychain 許可ダイアログは「常に許可」）
python3 collector/ai_usage.py show              # 取れた値を確認
```

片方だけ失敗しても、もう一方は更新されます（失敗側は前回値を「stale」として保持）。

### 2. iPhone（Mac の Scriptable でも同じ）

1. App Store で **Scriptable** を入れ、新規スクリプト `AIUsage` を作って `iphone/AIUsage.js` の中身を貼る。
2. ファイルブックマークの登録は**不要**です（ウィジェットは iCloud Drive の `Scriptable` フォルダにある `usage.json` を直接読む）。iPhone の「ファイル」アプリで iCloud Drive/Scriptable に `usage.json` があるか確認してください。
3. ホーム画面に Scriptable ウィジェット（中サイズ）を追加 → 長押し > ウィジェットを編集 > Script で `AIUsage`。

Gemini の Claude+GPT 枠も出したい場合は `AIUsage.js` の `geminiGroups` を `null` にします。

## 動かないとき

| 症状 | 確認 |
|---|---|
| `Claude のトークンが無効/期限切れ` | Claude Code を一度起動（トークン更新は Claude Code 側に任せ、本ツールは更新しない設計） |
| Claude の認証情報が見つからない | macOS では Keychain の `Claude Code-credentials`（サービス名は未検証）または `~/.claude/.credentials.json`。環境変数 `CLAUDE_OAUTH_TOKEN` でも指定可 |
| `agy が見つかりません` | `ANTIGRAVITY_CLI_PATH` を指定、または PATH を確認 |
| Gemini が「出力を解釈できません」「バケットがありません」 | `python3 collector/ai_usage.py raw gemini` の結果を確認（`agy` のバージョン更新で形式が変わった可能性） |
| Claude の accessToken を取得できません | `python3 collector/ai_usage.py diagnose`（トークンの値は出さず、構造だけ表示） |
| ウィジェットが `null is not an object (data.providers)` で落ちる（旧版） | 自動更新時に iCloud 上の usage.json が未取得で読み込みが空になるのが原因。現行版は読めた値を端末内にキャッシュし、読めない時はそれを「（キャッシュ）」付きで表示する。直らなければスクリプトを最新版に貼り直す |
| ウィジェットに「usage.json がありません」 | Mac で `python3 collector/ai_usage.py collect` を実行し、出力の `wrote:` に `iCloud~dk~simonbs~Scriptable` を含むパスがあるか確認。なければ Mac のターミナルで `ls ~/Library/Mobile\ Documents/ | grep -i scriptable` |
| iPhone が「データなし/古い」 | Mac のスリープ・iCloud Drive の同期・`~/.ai-usage/collector.log` を確認 |

## 開発

```bash
python3 -m unittest discover -s tests -v
```

アンインストール: `./install.sh --uninstall`

## 参考にした公開情報

- CodexBar（steipete/CodexBar）: `docs/gemini.md`, `docs/antigravity.md`
- Claude usage エンドポイントの公開実装（ccometixline #95、claude-code-usage-monitor #202 ほか）
