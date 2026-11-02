"""パーサのテスト。

- Gemini: AGY_REAL は実機（agy -p /usage --output-format json）の出力そのもの（2026-11-02 取得）。
- Claude: 入力は公開情報の項目名から作った合成データ（実機の Claude 応答は未確認）。
実機で形が違えば `ai_usage.py raw <provider>` の結果を基にフィクスチャとパーサを直すこと。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collector"))
import ai_usage as au  # noqa: E402


class ClaudeParse(unittest.TestCase):
    def test_five_hour_and_weekly(self):
        resp = {
            "five_hour": {"utilization": 2.0, "resets_at": "2026-11-02T18:00:00+00:00"},
            "seven_day": {"utilization": 85.0, "resets_at": "2026-11-05T01:00:00+00:00"},
            "seven_day_opus": None,
        }
        w = au.parse_claude(resp)
        self.assertEqual([x["kind"] for x in w], ["five_hour", "weekly"])
        self.assertEqual(w[0]["remaining_percent"], 98.0)
        self.assertEqual(w[1]["remaining_percent"], 15.0)
        self.assertEqual(w[0]["resets_at"], "2026-11-02T18:00:00+00:00")

    def test_extra_model_window_is_kept_as_other(self):
        resp = {"five_hour": {"utilization": 0}, "seven_day_fable": {"utilization": 0.0, "resets_at": "x"}}
        kinds = {x["id"]: x["kind"] for x in au.parse_claude(resp)}
        self.assertEqual(kinds["seven_day_fable"], "other")

    def test_promo_credit_field_is_not_a_rate_window(self):
        # 実機で iguana_necktie が「残り0%の枠」として出た不具合の回帰テスト
        resp = {"five_hour": {"utilization": 13.0, "resets_at": "a"},
                "seven_day": {"utilization": 5.0, "resets_at": "b"},
                "iguana_necktie": {"utilization": 100.0, "resets_at": "c"},
                "omelette_promotional": {"utilization": 100.0}}
        ids = [x["id"] for x in au.parse_claude(resp)]
        self.assertEqual(ids, ["five_hour", "seven_day"])

    def test_per_model_weekly_label(self):
        w = au.parse_claude({"five_hour": {"utilization": 1}, "seven_day_opus": {"utilization": 40}})
        opus = [x for x in w if x["id"] == "seven_day_opus"][0]
        self.assertEqual((opus["label"], opus["kind"], opus["remaining_percent"]), ("週間 Opus", "other", 60.0))

    def test_over_100_is_clamped(self):
        w = au.parse_claude({"five_hour": {"utilization": 130}})
        self.assertEqual(w[0]["remaining_percent"], 0.0)

    def test_empty_raises(self):
        with self.assertRaises(au.ProviderError):
            au.parse_claude({"five_hour": None})

    def test_token_from_credentials(self):
        self.assertEqual(au.token_from_credentials({"claudeAiOauth": {"accessToken": "t"}}), "t")
        with self.assertRaises(au.ProviderError):
            au.token_from_credentials({})


AGY_REAL = {
    "status": "SUCCESS",
    "command": {"name": "usage", "data": {"groups": [
        {"name": "Gemini Models", "buckets": [
            {"id": "gemini-weekly", "name": "Weekly Limit Remaining",
             "description": "You have used some of your weekly limit, it will fully refresh in 4 days, 19 hours.",
             "window": "weekly", "remaining_fraction": 0.966158926486969, "reset_time": "2026-11-07T13:33:59Z"},
            {"id": "gemini-5h", "name": "Five Hour Limit Remaining", "window": "5h",
             "remaining_fraction": 1, "reset_time": "2026-11-02T22:37:55Z"}]},
        {"name": "Claude and GPT models", "buckets": [
            {"id": "3p-weekly", "name": "Weekly Limit Remaining", "window": "weekly",
             "remaining_fraction": 1, "reset_time": "2026-11-09T17:37:55Z"},
            {"id": "3p-5h", "name": "Five Hour Limit Remaining", "window": "5h",
             "remaining_fraction": 1, "reset_time": "2026-11-02T22:37:55Z"}]},
        {"name": "Gemini 4 Argon", "buckets": [
            {"id": "gemini-argon-wtu-weekly", "name": "Gemini 4 Argon Weekly Limit", "window": "weekly",
             "remaining_fraction": 1, "reset_time": "2026-11-09T04:38:15Z"}]},
    ]}},
}


class AgyParse(unittest.TestCase):
    def test_real_output(self):
        w = au.parse_agy_usage(AGY_REAL)
        self.assertEqual([(x["group"], x["kind"]) for x in w], [
            ("Gemini", "five_hour"), ("Gemini", "weekly"),
            ("Claude+GPT", "five_hour"), ("Claude+GPT", "weekly"),
            ("Gemini 4 Argon", "weekly"),
        ])
        self.assertEqual(w[0]["remaining_percent"], 100.0)
        self.assertEqual(w[1]["remaining_percent"], 96.6)
        self.assertEqual(w[1]["resets_at"], "2026-11-07T13:33:59Z")
        self.assertIn("4 days", w[1]["reset_text"])

    def test_argon_is_not_merged_into_gemini(self):
        ids = [x["id"] for x in au.parse_agy_usage(AGY_REAL)]
        self.assertEqual(len(ids), len(set(ids)))
        gemini = [x for x in au.parse_agy_usage(AGY_REAL) if x["group"] == "Gemini"]
        self.assertEqual(len(gemini), 2)

    def test_docs_style_keys_still_accepted(self):
        data = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"bucketId": "g", "window": "WEEKLY", "remaining": {"remainingFraction": 0.5},
             "resetTime": "2026-11-07T00:00:00Z"}]}]}
        w = au.parse_agy_usage(data)
        self.assertEqual((w[0]["kind"], w[0]["remaining_percent"], w[0]["resets_at"]),
                         ("weekly", 50.0, "2026-11-07T00:00:00Z"))

    def test_unknown_remaining_is_dropped_not_invented(self):
        data = {"groups": [{"name": "Gemini Models", "buckets": [
            {"id": "a", "window": "weekly", "reset_time": "2026-11-05T00:00:00Z"}]}]}
        with self.assertRaises(au.ProviderError):
            au.parse_agy_usage(data)

    def test_json_loose(self):
        self.assertEqual(au.parse_json_loose('noise\n{"a": 1}\n'), {"a": 1})
        with self.assertRaises(au.ProviderError):
            au.parse_json_loose("not json")


class ClaudeToken(unittest.TestCase):
    def test_empty_keychain_token_falls_back_to_file(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "FILE_TOKEN"}}))
            fake = mock.Mock(returncode=0, stdout=json.dumps({"claudeAiOauth": {"accessToken": ""}}))
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": d}, clear=False), \
                 mock.patch.object(au.sys, "platform", "darwin"), \
                 mock.patch.object(au.subprocess, "run", return_value=fake):
                os.environ.pop("CLAUDE_OAUTH_TOKEN", None)
                self.assertEqual(au.read_claude_token(), "FILE_TOKEN")

    def test_diagnose_never_prints_values(self):
        masked = au._mask({"claudeAiOauth": {"accessToken": "SECRET123", "expiresAt": 1730000000, "scopes": ["a"]}})
        self.assertNotIn("SECRET123", json.dumps(masked))
        self.assertEqual(masked["claudeAiOauth"]["accessToken"], "<str len=9>")


class Outputs(unittest.TestCase):
    def test_scriptable_dir_is_added_only_when_it_exists(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            orig = (au.LOCAL_DIR, au.ICLOUD_DIR, au.SCRIPTABLE_DIR)
            try:
                au.LOCAL_DIR, au.ICLOUD_DIR = d / "local", d / "none" / "AIUsage"
                au.SCRIPTABLE_DIR = d / "Scriptable"
                self.assertEqual(au.default_outputs(), [d / "local" / "usage.json"])
                au.SCRIPTABLE_DIR.mkdir()
                self.assertIn(au.SCRIPTABLE_DIR / "usage.json", au.default_outputs())
            finally:
                au.LOCAL_DIR, au.ICLOUD_DIR, au.SCRIPTABLE_DIR = orig


class Aggregate(unittest.TestCase):
    def test_failure_keeps_previous_as_stale(self):
        prev = {"gemini": {"fetched_at": "t0", "windows": [{"id": "x"}]}}
        orig = au.PROVIDERS["gemini"]
        au.PROVIDERS["gemini"] = lambda: (_ for _ in ()).throw(au.ProviderError("boom"))
        try:
            out = au.collect_all(["gemini"], prev)
        finally:
            au.PROVIDERS["gemini"] = orig
        g = out["gemini"]
        self.assertFalse(g["ok"])
        self.assertTrue(g["stale"])
        self.assertEqual(g["windows"], [{"id": "x"}])
        self.assertEqual(g["error"], "boom")

    def test_collect_writes_file_and_one_provider_may_fail(self):
        orig = dict(au.PROVIDERS)
        au.PROVIDERS["claude"] = lambda: [{"id": "five_hour", "label": "5時間", "kind": "five_hour",
                                           "group": "Claude", "used_percent": 1.0,
                                           "remaining_percent": 99.0, "resets_at": None, "reset_text": None}]
        au.PROVIDERS["gemini"] = lambda: (_ for _ in ()).throw(au.ProviderError("no agy"))
        try:
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "u.json"
                rc = au.main(["collect", "--out", str(out)])
                data = json.loads(out.read_text())
        finally:
            au.PROVIDERS.clear()
            au.PROVIDERS.update(orig)
        self.assertEqual(rc, 0)  # 片方成功なら 0
        self.assertTrue(data["providers"]["claude"]["ok"])
        self.assertFalse(data["providers"]["gemini"]["ok"])
        self.assertEqual(data["version"], 1)


if __name__ == "__main__":
    unittest.main()
