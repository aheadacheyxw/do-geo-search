import json
import tempfile
import unittest
from pathlib import Path

from geo_monitoring.period_report import compare_runs, write_comparison
from geo_monitoring.final_report import build_and_render


def make_run(root: Path, run_id: str, mentioned: bool, official: bool):
    run = root / run_id
    (run / "step4").mkdir(parents=True)
    (run / "step5").mkdir()
    manifest = {
        "run_id": run_id, "project_id": "example", "profile_version": "p1",
        "project_profile_sha256": "hash", "question_catalog_version": "c1",
        "question_set_version": "s1",
        "planned_questions": [{"question_id": "q1", "exact_question_text": "有哪些服务商？", "question_group": "scenario_provider_recommendation"}],
    }
    snapshot = {"snapshot_id": run_id + "-snapshot", "project_id": "example", "competitor_catalog_version": "r1", "signal_definition_version": "v1"}
    row = {
        "run_id": run_id, "observation_id": run_id + "-o1", "evidence_id": run_id + "-e1",
        "question_id": "q1", "platform": "ExampleAI", "brand_mention": mentioned,
        "mention_eligible": True, "citation_eligible": True, "citation_status": "verified",
        "rank_eligible": False, "trend_eligible": True, "comparable": True,
        "brand_sentiment": "unavailable",
        "measurement_context": {"market_region": "CN", "language_locale": "zh-CN", "platform_product_surface": "web", "account_session_class": "monitor", "web_search_state": "on", "mode_reasoning_state": "default", "collection_mode": "ui"},
    }
    citations = [{"visible_url": "https://example.com/page", "visible_anchor_text": "官方说明"}] if official else [{"visible_url": "https://source.example/page", "visible_anchor_text": "第三方"}]
    decision = {"observation_id": row["observation_id"], "citation": {"verified_citations": citations}}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "step5" / "step5_snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (run / "step4" / "normalized_observations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (run / "step4" / "signal_decisions.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")
    return run


class PeriodReportTests(unittest.TestCase):
    def test_strict_comparison_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            previous = make_run(root, "previous", False, False)
            current = make_run(root, "current", True, True)
            profile = {"target_scope": {"canonical_brand": "示例品牌", "official_domains": ["example.com"]}}
            report = compare_runs(previous, current, profile)
            self.assertEqual("comparable", report["status"])
            self.assertEqual(100.0, report["strict_comparable_metrics"]["brand_mention"]["change_pp"])
            self.assertEqual(1, report["strict_comparable_metrics"]["official_domain_citation_presence"]["current_count"])
            output = root / "comparison"
            write_comparison(report, output, "示例品牌")
            page = (output / "GEO_周期对比报告.html").read_text(encoding="utf-8")
            self.assertIn("示例品牌 GEO 周期对比", page)
            self.assertIn("不可比较 / 不可评估", page)

    def test_brand_neutral_single_period_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = make_run(root, "current", True, True)
            profile = {"target_scope": {"canonical_brand": "示例品牌", "official_domains": ["example.com"]}}
            page_path = build_and_render(current, profile, current / "report")
            page = page_path.read_text(encoding="utf-8")
            self.assertIn("示例品牌 GEO 监测总报告", page)
            self.assertIn("明确推荐率", page)
            self.assertIn("官方域引用出现率", page)


if __name__ == "__main__":
    unittest.main()
