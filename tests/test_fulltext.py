import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from review_writer.fulltext import (
    DownloadRequest,
    FullTextDownloader,
    PDFVerification,
    canonical_status,
    classify_evidence,
    export_school_profile,
    parse_download_output,
    verify_pdf,
)
from review_writer.settings import LibrarySettings
from review_writer.workflow_models import EvidenceLevel


class FullTextCoreTests(unittest.TestCase):
    def test_parses_compact_json_and_normalizes_legacy_status(self) -> None:
        payload = parse_download_output(
            'local diagnostic\n{"summary":{"total":1,"downloaded":0,"seconds":0.1},'
            '"results":[{"status":"needs_user_login"}]}\n'
        )

        self.assertEqual(payload["summary"]["total"], 1)
        self.assertEqual(canonical_status(payload["results"][0]["status"]), "carsi_waiting_user")
        self.assertEqual(canonical_status("made_up_status"), "failed_after_retry")

    def test_exports_only_non_secret_library_route(self) -> None:
        settings = LibrarySettings(
            portal_url="https://library.example.edu/resources?service=wos&access_token=secret",
            web_of_science_url="https://www.webofscience.com/search",
            cnki_url="https://kns.cnki.net/",
        )
        with TemporaryDirectory() as directory:
            path = export_school_profile(settings, Path(directory))
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)

        self.assertNotIn("secret", raw)
        self.assertNotIn("access_token", raw)
        self.assertEqual(payload["source"], "review-writer")
        self.assertEqual(payload["discovery"]["web_of_science_url"], settings.web_of_science_url)

    def test_builds_explicit_small_batch_commands(self) -> None:
        settings = LibrarySettings(
            portal_url="https://library.example.edu/resources",
            cdp_proxy_url="http://127.0.0.1:3456",
            max_batch_size=5,
            pdf_only=True,
        )
        with TemporaryDirectory() as directory:
            downloader = FullTextDownloader(
                Path(directory), settings, node_path=Path(sys.executable)
            )
            commands = downloader.build_commands(
                [
                    DownloadRequest(paper_id="P1", doi="10.1000/ABC"),
                    DownloadRequest(paper_id="P2", doi="https://doi.org/10.1000/xyz"),
                    DownloadRequest(paper_id="P3", title="中文精确题名"),
                    DownloadRequest(
                        paper_id="P4",
                        title="Readable name",
                        pdf_url="https://example.org/open.pdf",
                    ),
                ]
            )

        self.assertEqual(len(commands), 3)
        doi_command, doi_requests = commands[0]
        self.assertIn("10.1000/abc,10.1000/xyz", doi_command)
        self.assertEqual(len(doi_requests), 2)
        chinese_command = commands[1][0]
        self.assertIn("--cnki-format", chinese_command)
        pdf_command = commands[2][0]
        self.assertIn("--pdf-url", pdf_command)
        # The bundled parser currently treats --title + --pdf-url as two modes.
        self.assertNotIn("Readable name", pdf_command)

    def test_rejects_oversized_or_ambiguous_batches(self) -> None:
        settings = LibrarySettings(max_batch_size=1, cdp_proxy_url="http://127.0.0.1:3456")
        with TemporaryDirectory() as directory:
            downloader = FullTextDownloader(
                Path(directory), settings, node_path=Path(sys.executable)
            )
            with self.assertRaises(ValueError):
                downloader.build_commands(
                    [DownloadRequest(doi="10.1000/a"), DownloadRequest(doi="10.1000/b")]
                )
        with self.assertRaises(ValueError):
            DownloadRequest(doi="10.1000/a", pdf_url="https://example.org/a.pdf").validated()

    def test_blank_pdf_is_valid_but_requires_ocr_and_cannot_be_full_text_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "blank.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=200)
            with path.open("wb") as handle:
                writer.write(handle)

            verification = verify_pdf(path, expected_title="Expected title")

        self.assertTrue(verification.valid_pdf)
        self.assertEqual(verification.page_count, 1)
        self.assertTrue(verification.ocr_needed)
        self.assertEqual(
            classify_evidence(verification=verification, abstract="Available abstract"),
            EvidenceLevel.ABSTRACT_ONLY,
        )

    def test_non_pdf_response_is_never_accepted_as_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "login.pdf"
            path.write_text("<html>please sign in</html>", encoding="utf-8")
            result = verify_pdf(path)

        self.assertFalse(result.valid_pdf)
        self.assertEqual(result.status, "pdf_corrupt")

    @patch("review_writer.fulltext.subprocess.run")
    def test_download_status_preserves_user_handoff(self, run: Mock) -> None:
        settings = LibrarySettings(
            portal_url="https://library.example.edu/resources",
            cdp_proxy_url="http://127.0.0.1:3456",
        )
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "summary": {"total": 1, "downloaded": 0, "seconds": 0.2},
                    "results": [
                        {
                            "doi": "10.1000/a",
                            "status": "publisher_verification_waiting_user",
                            "reason": "CAPTCHA requires user",
                            "url": "https://publisher.example/article?session=private&view=full",
                        }
                    ],
                }
            ),
            stderr="",
        )
        with TemporaryDirectory() as directory:
            downloader = FullTextDownloader(
                Path(directory), settings, node_path=Path(sys.executable)
            )
            result = downloader.download([DownloadRequest(paper_id="P1", doi="10.1000/a")])

        self.assertTrue(result.results[0].needs_user_action)
        self.assertEqual(result.results[0].status, "publisher_verification_waiting_user")
        self.assertNotIn("session", result.results[0].url)
        self.assertIn("view=full", result.results[0].url)
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertIn("LIT_DL_CONFIG_DIR", environment)

    @patch("review_writer.fulltext.subprocess.run")
    def test_open_access_title_does_not_require_library_profile(self, run: Mock) -> None:
        run.return_value = Mock(
            returncode=0,
            stdout=json.dumps(
                {
                    "summary": {"total": 1, "downloaded": 0, "seconds": 0.1},
                    "results": [{"status": "no_authorized_pdf_found"}],
                }
            ),
            stderr="",
        )
        settings = LibrarySettings(
            portal_url="",
            cdp_proxy_url="http://127.0.0.1:3456",
        )
        with TemporaryDirectory() as directory:
            downloader = FullTextDownloader(
                Path(directory), settings, node_path=Path(sys.executable)
            )
            result = downloader.download(
                [DownloadRequest(paper_id="P1", title="Exact open title", open_access=True)]
            )

        self.assertEqual(result.results[0].status, "no_authorized_pdf_found")

    def test_title_mismatch_withholds_full_text_evidence(self) -> None:
        verification = PDFVerification(
            path="other.pdf",
            status="downloaded",
            valid_pdf=True,
            page_count=3,
            extracted_characters=1000,
            pages_with_text=3,
            ocr_needed=False,
            title_match=False,
        )

        self.assertEqual(
            classify_evidence(verification=verification, abstract="Available abstract"),
            EvidenceLevel.ABSTRACT_ONLY,
        )


if __name__ == "__main__":
    unittest.main()
