import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import yaml

from common.config_loader import CONFIG_NAMES, DEFAULT_DIRECTORY, ConfigError, ConfigLoader, main


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for name in CONFIG_NAMES:
            shutil.copyfile(DEFAULT_DIRECTORY / f"{name}.yaml", self.directory / f"{name}.yaml")
        self.loader = ConfigLoader(self.directory)

    def rewrite(self, name, transform):
        path = self.directory / f"{name}.yaml"
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        transform(value)
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def test_repository_files_load_without_changing_values(self):
        snapshot = self.loader.snapshot()
        self.assertEqual(set(snapshot.configs), set(CONFIG_NAMES))
        self.assertEqual(len(snapshot.revision), 64)
        self.assertIsNotNone(snapshot.loaded_at.tzinfo)
        for name in CONFIG_NAMES:
            expected = yaml.safe_load((DEFAULT_DIRECTORY / f"{name}.yaml").read_text(encoding="utf-8"))
            self.assertEqual(snapshot.to_dict()[name], expected)

    def test_changed_values_reload_without_restarting_instance(self):
        before = self.loader.snapshot()
        self.rewrite("thresholds", lambda d: d.update(release_hours=8))
        after = self.loader.snapshot()
        self.assertNotEqual(before.revision, after.revision)
        self.assertEqual(before["thresholds"]["release_hours"], 6)
        self.assertEqual(after["thresholds"]["release_hours"], 8)
        self.assertIs(self.loader.last_good, after)

    def test_unchanged_snapshot_is_cached_and_manual_reload_revalidates(self):
        before = self.loader.snapshot()
        self.assertIs(self.loader.snapshot(), before)
        reloaded = self.loader.reload()
        self.assertIsNot(reloaded, before)
        self.assertEqual(reloaded.revision, before.revision)

    def test_same_size_same_mtime_edit_is_detected(self):
        before = self.loader.snapshot()
        path = self.directory / "thresholds.yaml"
        old_stat = path.stat()
        text = path.read_bytes().replace(b"release_hours: 6", b"release_hours: 8")
        path.write_bytes(text)
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertNotEqual(self.loader.snapshot().revision, before.revision)
        self.assertEqual(self.loader.get("thresholds")["release_hours"], 8)

    def test_atomic_bundle_rejects_partial_update_and_recovers(self):
        before = self.loader.snapshot()
        original_costs = (self.directory / "costs.yaml").read_bytes()
        self.rewrite("thresholds", lambda d: d.update(release_hours=8))
        (self.directory / "costs.yaml").write_text("bad: [", encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "costs.yaml"):
            self.loader.snapshot()
        self.assertIs(self.loader.last_good, before)
        self.assertEqual(before["thresholds"]["release_hours"], 6)
        self.assertIsNotNone(self.loader.last_error)
        (self.directory / "costs.yaml").write_bytes(original_costs)
        after = self.loader.snapshot()
        self.assertEqual(after["thresholds"]["release_hours"], 8)
        self.assertIsNone(self.loader.last_error)

    def test_failed_initial_load_does_not_create_fallback(self):
        (self.directory / "first_aid.yaml").unlink()
        with self.assertRaisesRegex(ConfigError, "first_aid.yaml"):
            self.loader.snapshot()
        self.assertIsNone(self.loader.last_good)

    def test_invalid_yaml_shapes_tags_duplicates_aliases_and_encoding(self):
        path = self.directory / "thresholds.yaml"
        invalid = [b"", b"null", b"[]", b"- x", b"x: [", b"x: 1\nx: 2\n",
                   b"x: {z: 1, z: 2}", b"1: value", b"x: &a [1]\ny: *a",
                   b"x: !!python/object:builtins.object {}", b"x: .nan", b"x: .inf", b"\xff"]
        for content in invalid:
            with self.subTest(content=content):
                path.write_bytes(content)
                with self.assertRaises(ConfigError):
                    self.loader.reload()
        self.assertIsNone(self.loader.last_good)

    def test_threshold_ranges_types_and_order(self):
        mutations = [
            lambda d: d.update(release_hours=-1), lambda d: d.update(release_hours=True),
            lambda d: d.update(release_hours="6"), lambda d: d.update(cycle_minutes=0),
            lambda d: d["levels"].update(주의=80), lambda d: d["levels"].pop("주의"),
            lambda d: d["fusion"].update(gate_low=0.8, full_at=0.7),
            lambda d: d["quality"].update(clahe_min=0.8, clahe_max=0.6),
            lambda d: d["baseline"].update(day_start="25:00"),
            lambda d: d["baseline"].update(day_end="06:00"),
            lambda d: d["baseline"].update(min_windows=0),
            lambda d: d.update(unknown_key=123),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                shutil.copyfile(DEFAULT_DIRECTORY / "thresholds.yaml", self.directory / "thresholds.yaml")
                self.rewrite("thresholds", mutate)
                with self.assertRaisesRegex(ConfigError, "thresholds.yaml"):
                    self.loader.reload()

    def test_other_config_contract_errors_are_rejected(self):
        mutations = [
            ("cause_rules", lambda d: d["rules"].pop("R1")),
            ("cause_rules", lambda d: d["rules"]["R2"].update(first_action=[])),
            ("release_rules", lambda d: d["size_groups"].pop("fry")),
            ("release_rules", lambda d: d["subsidy"].update(cap_per_farm_krw=-1)),
            ("costs", lambda d: d["size_groups"]["mid"].update(avg_weight_kg=0)),
            ("costs", lambda d: d["expected_mortality_by_level"].update(danger=2)),
            ("costs", lambda d: d["action_cost_krw"].pop("keep")),
            ("costs", lambda d: d["option_loss"]["release"].update(mortality_factor=0.5)),
            ("first_aid", lambda d: d["blocked_terms"].update(dose_patterns=["["])),
            ("first_aid", lambda d: d["common_first_aid"].append(d["common_first_aid"][0].copy())),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                self.rewrite(name, mutate)
                with self.assertRaises(ConfigError):
                    self.loader.reload()
                shutil.copyfile(DEFAULT_DIRECTORY / f"{name}.yaml", self.directory / f"{name}.yaml")

    def test_rule_specific_extensions_are_preserved(self):
        self.rewrite("cause_rules", lambda d: d["rules"]["R2"]["behavior"].update(cohesion_drop=0.3, lookback_h=12))
        self.assertEqual(self.loader.snapshot()["cause_rules"]["rules"]["R2"]["behavior"]["lookback_h"], 12)

    def test_immutable_snapshot_and_independent_mutable_copy(self):
        snapshot = self.loader.snapshot()
        with self.assertRaises(TypeError):
            snapshot["thresholds"]["levels"]["주의"] = 10
        self.assertIsInstance(snapshot["first_aid"]["common_first_aid"], tuple)
        copy = snapshot.to_dict()
        copy["thresholds"]["levels"]["주의"] = 10
        copy["first_aid"]["common_first_aid"].clear()
        self.assertEqual(snapshot["thresholds"]["levels"]["주의"], 40)
        self.assertEqual(len(snapshot["first_aid"]["common_first_aid"]), 5)

    def test_unknown_names_do_not_read_arbitrary_paths(self):
        with self.assertRaises(KeyError):
            self.loader.get("../requirements")
        with self.assertRaises(KeyError):
            self.loader.get("thresholds.yaml")

    def test_reader_detects_files_changing_during_validation(self):
        before = self.loader.snapshot()
        read = self.loader._read_files
        calls = 0
        def changing_read():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.rewrite("thresholds", lambda d: d.update(release_hours=8))
            return read()
        with patch.object(self.loader, "_read_files", side_effect=changing_read):
            with self.assertRaisesRegex(ConfigError, "changed while loading"):
                self.loader.reload()
        self.assertIs(self.loader.last_good, before)

    def test_concurrent_readers_receive_one_complete_revision(self):
        self.loader.snapshot()
        self.rewrite("thresholds", lambda d: d.update(release_hours=8))
        with ThreadPoolExecutor(max_workers=8) as pool:
            snapshots = list(pool.map(lambda _: self.loader.snapshot(), range(32)))
        self.assertEqual(len({id(s) for s in snapshots}), 1)
        self.assertTrue(all(s["thresholds"]["release_hours"] == 8 and len(s.configs) == 5 for s in snapshots))

    def test_default_path_does_not_depend_on_cwd(self):
        result = subprocess.run([sys.executable, "-m", "common.config_loader"], cwd=self.directory,
            env={**os.environ, "PYTHONPATH": str(DEFAULT_DIRECTORY.parent)},
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_file_size_limit_and_utf8_bom(self):
        path = self.directory / "thresholds.yaml"
        raw = path.read_bytes()
        path.write_bytes(b"\xef\xbb\xbf" + raw)
        self.assertEqual(self.loader.snapshot()["thresholds"]["cycle_minutes"], 5)
        path.write_bytes(b"#" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ConfigError, "1 MiB"):
            self.loader.snapshot()

    def test_cli_success_failure_and_errors_do_not_dump_values(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--directory", str(self.directory)]), 0)
        self.assertTrue(json.loads(out.getvalue())["ok"])
        self.rewrite("thresholds", lambda d: d.update(release_hours="private-value"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(["--directory", str(self.directory)]), 1)
        result = json.loads(out.getvalue())
        self.assertFalse(result["ok"])
        self.assertIn("release_hours", result["error"])
        self.assertNotIn("private-value", out.getvalue())


if __name__ == "__main__":
    unittest.main()
