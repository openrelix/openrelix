#!/usr/bin/env python3

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release_maintenance_branch  # noqa: E402


class ReleaseMaintenanceBranchTest(unittest.TestCase):
    def test_x_y_zero_release_creates_matching_bugfix_branch(self):
        self.assertEqual(
            release_maintenance_branch.maintenance_branch_for_version("0.4.0"),
            "bugfix/version_0_4",
        )
        self.assertEqual(
            release_maintenance_branch.maintenance_branch_for_version("1.0.0"),
            "bugfix/version_1_0",
        )

    def test_patch_and_prerelease_versions_do_not_create_branch(self):
        self.assertEqual(
            release_maintenance_branch.maintenance_branch_for_version("0.4.1"),
            "",
        )
        self.assertEqual(
            release_maintenance_branch.maintenance_branch_for_version("0.4.0-beta.1"),
            "",
        )

    def test_previous_branch_uses_latest_stable_tag_before_target(self):
        branch, tag = release_maintenance_branch.previous_maintenance_branch_for_version(
            "0.4.0",
            ["v0.2.10", "v0.3.8", "v0.4.0-beta.1", "v0.4.0"],
        )
        self.assertEqual(branch, "bugfix/version_0_3")
        self.assertEqual(tag, "v0.3.8")

    def test_previous_branch_crosses_major_boundary(self):
        branch, tag = release_maintenance_branch.previous_maintenance_branch_for_version(
            "1.0.0",
            ["v0.4.2", "v0.5.7"],
        )
        self.assertEqual(branch, "bugfix/version_0_5")
        self.assertEqual(tag, "v0.5.7")

    def test_previous_branch_is_empty_for_patch_or_first_release(self):
        self.assertEqual(
            release_maintenance_branch.previous_maintenance_branch_for_version(
                "0.4.1",
                ["v0.4.0"],
            ),
            ("", ""),
        )
        self.assertEqual(
            release_maintenance_branch.previous_maintenance_branch_for_version(
                "0.1.0",
                [],
            ),
            ("", ""),
        )


if __name__ == "__main__":
    unittest.main()
