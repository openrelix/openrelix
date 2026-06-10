#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from openrelix_overview import redaction  # noqa: E402


def sample_home_path():
    return "/" + "Users" + "/alice/Project/file.md"


class RedactionTests(unittest.TestCase):
    def test_restore_protected_text_restores_multiple_local_action_attrs(self):
        path = sample_home_path()
        parent = path.rsplit("/", 1)[0]
        html = (
            f'<button data-open-finder-path="{path}">'
            "Reveal"
            "</button>"
            f'<button data-resume-command="cd {parent} && codex">'
            "Resume"
            "</button>"
        )

        protected_html, protected = redaction.protect_local_execution_attrs(html)

        self.assertNotEqual(protected_html, html)
        self.assertEqual(len(protected), 2)
        self.assertEqual(redaction.restore_protected_text(protected_html, protected), html)

    def test_quarantine_project_path_attrs_are_kept_executable(self):
        path = sample_home_path().rsplit("/", 1)[0]
        html = (
            f'<li data-skill-quarantine-project-root="{path}">'
            f'<button data-skill-quarantine-project-path="{path}">'
            f"{path}"
            "</button>"
            f'<button data-skill-quarantine-project-candidate="{path}">'
            "Candidate"
            "</button>"
            "</li>"
        )

        normalized = redaction.normalize_brand_display_text(html)

        self.assertIn(f'data-skill-quarantine-project-root="{path}"', normalized)
        self.assertIn(f'data-skill-quarantine-project-path="{path}"', normalized)
        self.assertIn(f'data-skill-quarantine-project-candidate="{path}"', normalized)
        self.assertIn(">~/Project<", normalized)

    def test_normalize_brand_display_keeps_action_attrs_while_redacting_visible_text(self):
        path = sample_home_path()
        html = (
            f'<button data-open-finder-path="{path}">'
            f"{path}"
            "</button>"
        )

        normalized = redaction.normalize_brand_display_text(html)

        self.assertIn(f'data-open-finder-path="{path}"', normalized)
        self.assertIn(">~/Project/file.md<", normalized)


if __name__ == "__main__":
    unittest.main()
