"""パーサのテスト。

注意: ここの入力は「公開情報（CodexBar docs 等）に書かれた項目名」から作った合成データで、
実機の生レスポンスではない。実機出力で形が違えば `ai_usage.py raw <provider>` の結果を
基にフィクスチャとパーサを直すこと。
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


class AgyParse(unittest.TestCase):
    FIXTURE = {
        "groups": [
            {"displayName": "Gemini Models", "buckets": [
                {"bucketId": "gemini-five-hour", "displayName": "Five-hour limit", "window": "FIVE_HOUR",
                 "remaining": {"remainingFraction": 0.82}, "description": "Resets in 2h 14m"},
                {"bucketId": "gemini-weekly", "displayName": "Weekly limit", "window": "WEEKLY",
                 "remaining": {"remainingFraction": 0.65}, "description": "Resets in 6d 3h"},
            ]},
            {"displayName": "Claude and GPT models", "buckets": [
                {"bucketId": "cg-weekly", "displayName": "Weekly limit",
                 "remaining": {"remainingFraction": 1}},
                {"bucketId": "cg-five-hour", "displayName": "Five-hour limit",
                 "remaining": {"remainingFraction": 0.5}},
            ]},
        ]
    }

    def test_groups_and_order(self):
        w = au.parse_agy_usage(self.FIXTURE)
        self.assertEqual([(x["group"], x["kind"]) for x in w], [
            ("Gemini", "five_hour"), ("Gemini", "weekly"),
            ("Claude+GPT", "five_hour"), ("Claude+GPT", "weekly"),
        ])
        self.assertEqual(w[0]["remaining_percent"], 82.0)
        self.assertEqual(w[0]["used_percent"], 18.0)
        self.assertEqual(w[0]["reset_text"], "Resets in 2h 14m")

    def test_wrapped_in_other_keys(self):
        w = au.parse_agy_usage({"command": "usage", "report": self.FIXTURE})
        self.assertEqual(len(w), 4)

    def test_unknown_remaining_is_dropped_not_invented(self):
        data = {"groups": [{"displayName": "Gemini", "buckets": [
            {"bucketId": "a", "window": "weekly", "resetTime": "2026-11-05T00:00:00Z"}]}]}
        with self.assertRaises(au.ProviderError):
            au.parse_agy_usage(data)

    def test_cadence_falls_back_to_bucket_id(self):
        data = {"groups": [{"displayName": "Gemini", "buckets": [
            {"bucketId": "gemini_weekly", "remaining": {"remainingFraction": 0.3}}]}]}
        self.assertEqual(au.parse_agy_usage(data)[0]["kind"], "weekly")

    def test_json_loose(self):
        self.assertEqual(au.parse_json_loose('noise\n{"a": 1}\n'), {"a": 1})
        with self.assertRaises(au.ProviderError):
            au.parse_json_loose("not json")


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
