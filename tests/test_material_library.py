import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools.materials import backup_material_library, get_material_library


class MaterialLibraryToolTests(unittest.TestCase):
    def test_get_material_library_reads_temporary_library_alias(self) -> None:
        with patch(
            "src.tools.materials.client.send_command",
            return_value={"result": '{"source":"current"}'},
        ) as mocked_send:
            result = get_material_library(source="temporary")

        self.assertEqual(json.loads(result), {"source": "current"})
        script = mocked_send.call_args.args[0]
        self.assertIn('local requestedSource = "current"', script)
        self.assertIn("currentMaterialLibrary", script)
        self.assertEqual(mocked_send.call_args.kwargs["cmd_type"], "maxscript")

    def test_get_material_library_rejects_unknown_source(self) -> None:
        payload = json.loads(get_material_library(source="unknown"))

        self.assertIn("source must be one of", payload["error"])

    def test_backup_material_library_rejects_file_path_for_all_sources(self) -> None:
        payload = json.loads(
            backup_material_library(source="all", file_path=r"C:\tmp\materials.mat")
        )

        self.assertIn("file_path can only be used", payload["error"])

    def test_backup_material_library_uses_exact_path_for_single_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exact_path = Path(tmp) / "nested" / "combined.mat"
            with patch(
                "src.tools.materials.client.send_command",
                return_value={"result": '{"source":"combined","saved":[]}'},
            ) as mocked_send:
                result = backup_material_library(
                    source="combined",
                    file_path=str(exact_path),
                )

            self.assertEqual(json.loads(result), {"source": "combined", "saved": []})
            self.assertTrue(exact_path.parent.exists())
            script = mocked_send.call_args.args[0]
            self.assertIn('local requestedSource = "combined"', script)
            self.assertIn(str(exact_path).replace("\\", "/"), script)
            self.assertEqual(mocked_send.call_args.kwargs["cmd_type"], "maxscript")
            self.assertEqual(mocked_send.call_args.kwargs["timeout"], 45.0)

    def test_backup_material_library_flags_failed_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failed_result = {
                "source": "current",
                "saved": [
                    {
                        "source": "current",
                        "path": str(Path(tmp) / "current.mat"),
                        "count": 1,
                        "saved": False,
                        "skipped": False,
                        "error": "disk denied",
                    }
                ],
            }
            with patch(
                "src.tools.materials.client.send_command",
                return_value={"result": json.dumps(failed_result)},
            ):
                result = backup_material_library(source="current", backup_dir=tmp)

        payload = json.loads(result)
        self.assertEqual(payload["status"], "error")
        self.assertIn("backups failed", payload["error"])


if __name__ == "__main__":
    unittest.main()
