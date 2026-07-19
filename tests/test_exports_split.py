from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from review_writer.exports import (
    export_markdown_document,
    export_verification_report,
    export_verified_report,
)


class VerificationReportExportTests(unittest.TestCase):
    def test_markdown_export_is_independent_of_delivery_gate(self) -> None:
        report = "# 核验报告\n\n- C1：核验失败"
        with TemporaryDirectory() as directory:
            target = Path(directory) / "nested" / "verification.md"
            result = export_verification_report(report, target, format="markdown")

            self.assertEqual(result, target)
            self.assertEqual(target.read_text(encoding="utf-8"), report + "\n")

    def test_docx_export_uses_verification_report_title(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / "verification.docx"
            export_verification_report("# 核验结果\n\n需人工复核。", target, format="docx")

            with ZipFile(target) as archive:
                core = archive.read("docProps/core.xml").decode("utf-8")
                document = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("文献核验报告", core)
            self.assertIn("需人工复核。", document)

    @patch("review_writer.exports.write_pdf")
    def test_pdf_export_delegates_to_existing_renderer(self, write_pdf) -> None:
        target = Path("verification.pdf")
        write_pdf.return_value = target

        result = export_verification_report("# 核验报告", target, format="PDF")

        self.assertEqual(result, target)
        write_pdf.assert_called_once_with("# 核验报告", target, title="文献核验报告")

    def test_common_export_rejects_unknown_format(self) -> None:
        with self.assertRaisesRegex(ValueError, "csv"):
            export_markdown_document("report", Path("report.csv"), format="csv")

    def test_existing_verified_report_still_enforces_strict_gate(self) -> None:
        with TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                export_verified_report(
                    "# 总结",
                    [{"claim_id": "C1", "status": "fail"}],
                    Path(directory) / "blocked.md",
                    format="md",
                )


if __name__ == "__main__":
    unittest.main()
