import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import mega


class MegaLinkTests(unittest.TestCase):
    def test_validate_modern_file_and_folder_links(self):
        self.assertEqual(
            mega.validate_mega_link("https://mega.nz/file/file-id#file-key"),
            "https://mega.nz/file/file-id#file-key",
        )
        self.assertEqual(
            mega.validate_mega_link("https://mega.co.nz/#F!folder-id!folder-key"),
            "https://mega.co.nz/#F!folder-id!folder-key",
        )

    def test_rejects_non_mega_or_incomplete_links(self):
        for link in (
            "http://mega.nz/file/file-id#file-key",
            "https://example.com/file/file-id#file-key",
            "https://mega.nz/file/file-id",
            "https://mega.nz/collection/collection-id#key",
        ):
            with self.subTest(link=link):
                with self.assertRaises(ValueError):
                    mega.validate_mega_link(link)

    def test_redacts_modern_and_legacy_decryption_keys(self):
        self.assertEqual(
            mega.redact_mega_link("https://mega.nz/folder/folder-id#folder-key"),
            "https://mega.nz/folder/folder-id#[key redacted]",
        )
        self.assertEqual(
            mega.redact_mega_link("https://mega.nz/#F!folder-id!folder-key"),
            "https://mega.nz/#F!folder-id![key redacted]",
        )

    @patch("backend.mega._run_inspection")
    def test_inspection_maps_files_into_existing_plan_categories(self, run_inspection):
        run_inspection.return_value = {
            "kind": "folder",
            "name": "Shared Folder",
            "items": [
                {"handle": "123", "path": "Course/video.mp4", "size": 2048},
                {"handle": "456", "path": "Course/sample.mp4", "size": 512},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(mega, "DOWNLOAD_DIR", Path(temp_dir)):
                plan = mega.inspect_mega_link(
                    "https://mega.nz/folder/folder-id#folder-key",
                    ["*sample*"],
                )

        self.assertEqual(plan["source_type"], "mega")
        self.assertEqual(plan["destination_folder"], "Shared Folder")
        self.assertEqual([item["category"] for item in plan["items"]], ["approved", "bloat"])
        self.assertEqual(plan["items"][0]["id"], "mega:123")
        self.assertEqual(plan["items"][0]["sanitized_path"], "Course/video.mp4")

    @patch("backend.mega._run_inspection")
    def test_inspection_rejects_paths_escaping_download_root(self, run_inspection):
        run_inspection.return_value = {
            "kind": "folder",
            "name": "Shared Folder",
            "items": [{"handle": "123", "path": "../outside.txt", "size": 12}],
        }
        with self.assertRaises(RuntimeError):
            mega.inspect_mega_link("https://mega.nz/folder/folder-id#folder-key")

    @patch("backend.mega._run_inspection")
    def test_file_link_uses_a_clean_destination_folder_name(self, run_inspection):
        run_inspection.return_value = {
            "kind": "file",
            "name": "lecture.mp4",
            "items": [{"handle": "123", "path": "lecture.mp4", "size": 20}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(mega, "DOWNLOAD_DIR", Path(temp_dir)):
                plan = mega.inspect_mega_link("https://mega.nz/file/file-id#file-key")

        self.assertEqual(plan["destination_folder"], "lecture")

    def test_download_wrapper_streams_progress_and_passes_link_on_stdin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            helper = Path(temp_dir) / "fake-mega-helper"
            helper.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                "assert sys.argv[1:3] == ['download', '123']\n"
                "assert sys.stdin.readline().strip() == 'https://mega.nz/file/file-id#file-key'\n"
                "pathlib.Path(sys.argv[3]).write_bytes(b'data')\n"
                "print(json.dumps({'event': 'progress', 'downloaded': 4, 'total': 4}), flush=True)\n"
            )
            helper.chmod(0o755)
            output = Path(temp_dir) / "download.part"
            progress = []
            with patch.object(mega, "MEGA_HELPER", str(helper)):
                result = mega.download_mega_file(
                    "https://mega.nz/file/file-id#file-key",
                    "123",
                    output,
                    lambda downloaded, total: progress.append((downloaded, total)),
                )
            self.assertEqual(result, str(output))
            self.assertEqual(output.read_bytes(), b"data")
        self.assertEqual(progress, [(4, 4)])


if __name__ == "__main__":
    unittest.main()
