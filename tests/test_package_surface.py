#!/usr/bin/env python3

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackageSurfaceTests(unittest.TestCase):
    def package_files(self):
        payload = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        return payload.get("files", [])

    def development_harness_skill_dirs(self):
        return sorted(
            path
            for path in (ROOT / ".agents" / "skills").glob("openrelix-*-harness")
            if (path / "SKILL.md").is_file()
        )

    def test_npm_package_only_allows_memory_review_repo_skill(self):
        packaged_skill_entries = [
            entry for entry in self.package_files()
            if entry.startswith(".agents/skills/")
        ]

        self.assertEqual(packaged_skill_entries, [".agents/skills/memory-review/"])

    def test_development_harness_skills_are_explicitly_ignored(self):
        ignore_text = (ROOT / ".npmignore").read_text(encoding="utf-8")
        skill_dirs = self.development_harness_skill_dirs()

        self.assertGreaterEqual(len(skill_dirs), 1)
        for path in skill_dirs:
            rel = path.relative_to(ROOT).as_posix() + "/"
            self.assertIn(rel, ignore_text)


if __name__ == "__main__":
    unittest.main()
