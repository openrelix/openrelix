import importlib.util
import plistlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def load_openrelix_module():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("openrelix_cli_for_schedule_test", scripts_dir / "openrelix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OpenRelixScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openrelix = load_openrelix_module()

    def with_launch_root(self, launch_root):
        original = self.openrelix.launch_agent_path
        self.openrelix.launch_agent_path = lambda filename: launch_root / filename
        self.addCleanup(lambda: setattr(self.openrelix, "launch_agent_path", original))

    def write_plist(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            plistlib.dump(payload, handle)

    def read_plist(self, path):
        with path.open("rb") as handle:
            return plistlib.load(handle)

    def test_schedule_updates_existing_launch_agent_plists(self):
        with TemporaryDirectory() as tmpdir:
            launch_root = Path(tmpdir)
            self.with_launch_root(launch_root)
            overview_path = launch_root / self.openrelix.OVERVIEW_REFRESH_PLIST_NAME
            nightly_path = launch_root / self.openrelix.NIGHTLY_ORGANIZE_PLIST_NAME
            self.write_plist(
                overview_path,
                {
                    "Label": self.openrelix.OVERVIEW_REFRESH_LABEL,
                    "StartInterval": 3600,
                },
            )
            self.write_plist(
                nightly_path,
                {
                    "Label": self.openrelix.NIGHTLY_ORGANIZE_LABEL,
                    "StartCalendarInterval": {"Hour": 23, "Minute": 0},
                },
            )

            self.openrelix.set_plist_interval_minutes(self.openrelix.OVERVIEW_REFRESH_PLIST_NAME, 30)
            self.openrelix.set_plist_calendar_time(self.openrelix.NIGHTLY_ORGANIZE_PLIST_NAME, 22, 30)

            self.assertEqual(self.read_plist(overview_path)["StartInterval"], 1800)
            self.assertEqual(
                self.read_plist(nightly_path)["StartCalendarInterval"],
                {"Hour": 22, "Minute": 30},
            )

    def test_update_flags_preserve_existing_overview_interval(self):
        with TemporaryDirectory() as tmpdir:
            launch_root = Path(tmpdir)
            self.with_launch_root(launch_root)
            self.write_plist(
                launch_root / self.openrelix.OVERVIEW_REFRESH_PLIST_NAME,
                {
                    "Label": self.openrelix.OVERVIEW_REFRESH_LABEL,
                    "StartInterval": 1800,
                    "EnvironmentVariables": {"OPENRELIX_REFRESH_LEARN_MEMORY": "1"},
                },
            )

            flags = self.openrelix.detected_update_install_flags()

            self.assertIn("--enable-learning-refresh", flags)
            self.assertIn("--overview-refresh-interval-minutes", flags)
            self.assertEqual(flags[flags.index("--overview-refresh-interval-minutes") + 1], "30")


if __name__ == "__main__":
    unittest.main()
