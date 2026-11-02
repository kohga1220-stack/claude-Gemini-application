// icon-color: deep-blue; icon-glyph: chart-bar;
// AI Usage ウィジェット（Scriptable 用 / iPhone・Mac 共通）
// Mac の collector が iCloud Drive/Scriptable/usage.json に書いた値を読んで表示する。
// 使い方: Scriptable にこのファイルの中身を貼る（スクリプト名は AIUsage）→ ホーム画面に
//        Scriptable ウィジェット(中)を追加 → 長押し > ウィジェットを編集 > Script に「AIUsage」を選ぶ。

const CONFIG = {
  staleMinutes: 30,        // これより古いデータは「古い」表示
  // Gemini 側で表示するグループ。null なら全部（Claude+GPT 枠も）。
  geminiGroups: ["Gemini"],
};

const C = {
  bg: Color.dynamic(new Color("#F2F2F7"), new Color("#1C1C1E")),
  fg: Color.dynamic(new Color("#111111"), new Color("#FFFFFF")),
  sub: Color.dynamic(new Color("#6B6B70"), new Color("#98989D")),
  track: Color.dynamic(new Color("#D1D1D6"), new Color("#3A3A3C")),
  ok: new Color("#34C759"), warn: new Color("#FF9500"), bad: new Color("#FF3B30"),
  claude: new Color("#D97757"), gemini: new Color("#4285F4"),
};

function validData(j) { return j && typeof j === "object" && j.providers && typeof j.providers === "object"; }

async function loadData() {
  // Mac の collector が iCloud Drive/Scriptable/usage.json に書いた値を読む（ブックマーク登録は不要）。
  // ホーム画面の自動更新では iCloud 上のファイルが未取得で読めないことがあるため、
  // 読めた最新の値を端末内にキャッシュし、読めない時はそれを表示する（古ければ「古いデータ」表示）。
  const icloud = FileManager.iCloud();
  const local = FileManager.local();
  const p = icloud.joinPath(icloud.documentsDirectory(), "usage.json");
  const cachePath = local.joinPath(local.documentsDirectory(), "ai-usage-cache.json");

  try {
    if (icloud.fileExists(p)) {
      if (!icloud.isFileDownloaded(p)) await icloud.downloadFileFromiCloud(p);
      const raw = icloud.readString(p);          // 未取得だと null が返る
      if (raw) {
        const j = JSON.parse(raw);
        if (validData(j)) {
          try { local.writeString(cachePath, raw); } catch (e) {}
          return { data: j };
        }
      }
    }
  } catch (e) { /* キャッシュへ */ }

  try {
    if (local.fileExists(cachePath)) {
      const j = JSON.parse(local.readString(cachePath));
      if (validData(j)) return { data: j, fromCache: true };
    }
  } catch (e) { /* エラー表示へ */ }

  return { error: "usage.json を読めません。Mac の collector と iCloud の同期を確認し、アプリで▶を押してください" };
}

function remainingColor(r) { return r <= 10 ? C.bad : r <= 30 ? C.warn : C.ok; }

function countdown(w) {
  if (!w.resets_at) return w.reset_text || "";
  const sec = Math.floor((new Date(w.resets_at) - new Date()) / 1000);
  if (isNaN(sec)) return w.reset_text || "";
  if (sec <= 0) return "リセット済み";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d > 0 ? `${d}日${h}時間` : `${h}時間${m}分`;
}

function bar(width, frac, color) {
  const ctx = new DrawContext();
  ctx.size = new Size(width, 5); ctx.opaque = false; ctx.respectScreenScale = true;
  ctx.setFillColor(C.track);
  ctx.fillRect(new Rect(0, 0, width, 5));
  const w = Math.max(0, Math.min(1, frac)) * width;
  if (w > 0) { ctx.setFillColor(color); ctx.fillRect(new Rect(0, 0, w, 5)); }
  return ctx.getImage();
}

function addWindow(stack, w) {
  const col = stack.addStack(); col.layoutVertically();
  const label = col.addText(w.label); label.font = Font.systemFont(10); label.textColor = C.sub;
  const val = col.addText(`${Math.round(w.remaining_percent)}%`);
  val.font = Font.boldRoundedSystemFont(26); val.textColor = C.fg; val.minimumScaleFactor = 0.6;
  col.addSpacer(2);
  const b = col.addImage(bar(64, w.remaining_percent / 100, remainingColor(w.remaining_percent)));
  b.imageSize = new Size(64, 5);
  const cd = col.addText("⟳ " + countdown(w)); cd.font = Font.systemFont(9); cd.textColor = C.sub; cd.lineLimit = 1;
}

function addProvider(parent, name, title, accent, pr) {
  const s = parent.addStack(); s.layoutVertically();
  const head = s.addStack(); head.centerAlignContent();
  const t = head.addText(title); t.font = Font.semiboldSystemFont(13); t.textColor = accent;
  head.addSpacer();
  const r = head.addText("残り"); r.font = Font.systemFont(10); r.textColor = C.sub;
  s.addSpacer(4);
  if (!pr) { const e = s.addText("データなし"); e.font = Font.systemFont(11); e.textColor = C.sub; return; }
  let ws = (pr.windows || []).filter(w => w.kind === "five_hour" || w.kind === "weekly" || w.kind === "other");
  if (name === "gemini" && CONFIG.geminiGroups) ws = ws.filter(w => CONFIG.geminiGroups.includes(w.group));
  if (ws.length === 0) {
    const e = s.addText(pr.error || "データなし"); e.font = Font.systemFont(10); e.textColor = C.warn; e.lineLimit = 3; return;
  }
  const row = s.addStack(); row.spacing = 10;
  ws.slice(0, 2).forEach(w => addWindow(row, w));
  if (!pr.ok) {
    s.addSpacer(3);
    const e = s.addText("⚠︎ " + (pr.stale ? "前回値" : "取得失敗")); e.font = Font.systemFont(9); e.textColor = C.warn;
  }
}

async function build() {
  const w = new ListWidget();
  w.backgroundColor = C.bg; w.setPadding(12, 14, 10, 14);
  const { data, error, fromCache } = await loadData();
  if (error) {
    const t = w.addText(error); t.font = Font.systemFont(11); t.textColor = C.warn; return w;
  }
  const body = w.addStack();
  addProvider(body, "claude", "Claude", C.claude, data.providers.claude);
  body.addSpacer(16);
  addProvider(body, "gemini", "Gemini", C.gemini, data.providers.gemini);
  w.addSpacer();
  const gen = new Date(data.generated_at);
  const old = isNaN(gen) || (new Date() - gen) / 60000 > CONFIG.staleMinutes;
  const f = new DateFormatter(); f.dateFormat = "HH:mm";
  const foot = w.addText(isNaN(gen) ? "取得時刻不明" : `取得 ${f.string(gen)}${fromCache ? "（キャッシュ）" : ""}${old ? "  ⚠︎ 古いデータ" : ""}`);
  foot.font = Font.systemFont(9); foot.textColor = old ? C.warn : C.sub;
  w.refreshAfterDate = new Date(Date.now() + 1 * 60 * 1000);  // 早めの更新を希望（実際の更新タイミングは iOS が決める）
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget); else await widget.presentMedium();
Script.complete();
