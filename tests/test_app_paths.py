import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from review_writer.app_paths import projects_root, resource_path, user_data_root


class AppPathTests(unittest.TestCase):
    def test_source_checkout_keeps_existing_local_directories(self) -> None:
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("REVIEW_WRITER_DATA_DIR", None)
            os.environ.pop("REVIEW_WRITER_PROJECTS_DIR", None)

            self.assertEqual(user_data_root(), resource_path(".local"))
            self.assertEqual(projects_root(), resource_path("outputs"))

    def test_environment_overrides_are_respected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch.dict(
                os.environ,
                {
                    "REVIEW_WRITER_DATA_DIR": str(base / "state"),
                    "REVIEW_WRITER_PROJECTS_DIR": str(base / "projects"),
                },
            ):
                self.assertEqual(user_data_root(), (base / "state").resolve())
                self.assertEqual(projects_root(), (base / "projects").resolve())

    def test_frozen_build_uses_local_app_data_for_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {"LOCALAPPDATA": temporary}),
            ):
                os.environ.pop("REVIEW_WRITER_DATA_DIR", None)

                self.assertEqual(user_data_root(), Path(temporary) / "ReviewWriter")


if __name__ == "__main__":
    unittest.main()

