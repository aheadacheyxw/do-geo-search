import json
import tempfile
import unittest
from pathlib import Path

from geo_monitoring.cli import init_project, prepare_run, validate_project
from geo_monitoring.history import discover_history, discovery_summary


ROOT = Path(__file__).parents[1]


class GenericProjectTests(unittest.TestCase):
    def test_example_initializes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "project"
            self.assertEqual(0, init_project(ROOT / "templates" / "project_answers.example.json", output))
            self.assertEqual(0, validate_project(output))
            profile = json.loads((output / "step1" / "project_profile.json").read_text(encoding="utf-8"))
            self.assertEqual("示例品牌", profile["target_scope"]["canonical_brand"])
            self.assertIn("sentiment", profile["success_signals"])
            runs = Path(tmp) / "runs"
            self.assertEqual(0, prepare_run(output, "example-run-001", runs))
            manifest = json.loads((runs / "example-run-001" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(5, manifest["planned_observation_count"])
            self.assertEqual("示例品牌", manifest["brand_identity"]["canonical_brand"])

    def test_history_matches_brand_and_domain_without_reading_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = json.loads((ROOT / "templates" / "project_answers.example.json").read_text(encoding="utf-8"))
            current = {
                "project_id": profile["project_id"],
                "target_scope": {"canonical_brand": profile["canonical_brand"], "aliases": profile["aliases"], "official_domains": profile["official_domains"]},
                "in_scope_products_services": profile["products_services"],
            }
            profile_dir = root / "projects" / "example" / "step1"
            profile_dir.mkdir(parents=True)
            (profile_dir / "project_profile.json").write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
            run = root / "runs" / "example-2026-01-01"
            (run / "step4").mkdir(parents=True)
            (run / "step5").mkdir()
            (run / "manifest.json").write_text(json.dumps({"run_id": "example-2026-01-01", "project_id": profile["project_id"], "question_set_version": "q1"}), encoding="utf-8")
            (run / "step4" / "normalized_observations.jsonl").write_text(json.dumps({"platform": "ExampleAI"}) + "\n", encoding="utf-8")
            (run / "step5" / "step5_snapshot.json").write_text(json.dumps({"snapshot_id": "s1", "project_id": profile["project_id"]}), encoding="utf-8")
            candidates = discover_history(current, [root])
            self.assertEqual(1, len(candidates))
            self.assertEqual("exact_brand_domain", candidates[0]["match_type"])
            self.assertTrue(discovery_summary(candidates)["prompt_user_for_comparison"])


if __name__ == "__main__":
    unittest.main()
