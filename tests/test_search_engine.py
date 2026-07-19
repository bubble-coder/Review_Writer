import json
import unittest
from unittest.mock import patch

from review_writer.integrations.base import SearchPreviewItem
from review_writer.search_engine import (
    CrossrefProvider,
    ImaSearchProvider,
    OpenAlexProvider,
    SearchProviderError,
    ZoteroSearchProvider,
    deduplicate_papers,
    papers_match,
    render_bibtex,
    render_literature_catalog,
    search_all,
)
from review_writer.workflow_models import EvidenceLevel, PaperRecord


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.payload[:size] if size >= 0 else self.payload


class SearchProviderParsingTests(unittest.TestCase):
    @patch("review_writer.search_engine.urlopen")
    def test_openalex_follows_pagination_cursor(self, mocked: object) -> None:
        mocked.side_effect = [  # type: ignore[attr-defined]
            FakeResponse({"meta": {"next_cursor": "cursor-2", "count": 2}, "results": [{"id": "https://openalex.org/W1", "display_name": "First", "publication_year": 2024, "authorships": [], "primary_location": {}, "open_access": {}}]}),
            FakeResponse({"meta": {"next_cursor": None, "count": 2}, "results": [{"id": "https://openalex.org/W2", "display_name": "Second", "publication_year": 2023, "authorships": [], "primary_location": {}, "open_access": {}}]}),
        ]

        papers = OpenAlexProvider().search("evidence", limit=2)

        self.assertEqual([paper.title for paper in papers], ["First", "Second"])
        self.assertEqual(mocked.call_count, 2)  # type: ignore[attr-defined]

    @patch("review_writer.search_engine.urlopen")
    def test_openalex_parses_abstract_and_keeps_oa_as_unvalidated_candidate(self, mocked: object) -> None:
        mocked.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "results": [
                    {
                        "id": "https://openalex.org/W1",
                        "doi": "https://doi.org/10.1000/TEST",
                        "display_name": "Useful study",
                        "publication_year": 2024,
                        "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                        "abstract_inverted_index": {"Useful": [0], "evidence": [1]},
                        "primary_location": {
                            "landing_page_url": "https://example.test/paper",
                            "source": {"display_name": "Journal"},
                        },
                        "open_access": {"is_oa": True, "oa_status": "gold"},
                        "cited_by_count": 7,
                        "relevance_score": 2.0,
                    }
                ]
            }
        )

        papers = OpenAlexProvider(mailto="a@example.test").search(
            "useful evidence", start_year=2020, end_year=2026, limit=5
        )

        self.assertEqual(papers[0].doi, "10.1000/test")
        self.assertEqual(papers[0].abstract, "Useful evidence")
        self.assertEqual(papers[0].evidence_level, EvidenceLevel.ABSTRACT_ONLY)
        self.assertIn("尚未下载校验", papers[0].access_status)
        request = mocked.call_args.args[0]  # type: ignore[attr-defined]
        self.assertIn("from_publication_date%3A2020", request.full_url)

    @patch("review_writer.search_engine.urlopen")
    def test_crossref_parses_metadata(self, mocked: object) -> None:
        mocked.return_value = FakeResponse(  # type: ignore[attr-defined]
            {
                "message": {
                    "items": [
                        {
                            "title": ["Crossref paper"],
                            "author": [{"given": "Grace", "family": "Hopper"}],
                            "DOI": "10.2000/xyz",
                            "URL": "https://doi.org/10.2000/xyz",
                            "container-title": ["Computing Journal"],
                            "published": {"date-parts": [[2023, 2, 1]]},
                            "abstract": "<jats:p>An abstract.</jats:p>",
                            "is-referenced-by-count": 11,
                            "score": 5,
                        }
                    ]
                }
            }
        )

        paper = CrossrefProvider().search("computing", limit=1)[0]

        self.assertEqual(paper.authors, ["Grace Hopper"])
        self.assertEqual(paper.year, 2023)
        self.assertEqual(paper.abstract, "An abstract.")
        self.assertEqual(paper.journal, "Computing Journal")


class DedupAndExportTests(unittest.TestCase):
    def test_doi_first_and_title_author_fallback(self) -> None:
        first = PaperRecord(
            title="A reproducible clinical validation study",
            authors=["Smith, Jane"],
            doi="10.1000/ABC",
            source="Crossref",
        )
        same_doi = PaperRecord(
            title="Different deposited title",
            authors=["Other, A"],
            doi="https://doi.org/10.1000/abc",
            source="OpenAlex",
        )
        no_doi = PaperRecord(
            title="A reproducible clinical validation study",
            authors=["Jane Smith"],
            abstract="Full abstract from another source.",
            source="Zotero",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )
        conflicting_doi = PaperRecord(
            title=first.title,
            authors=first.authors,
            doi="10.1000/different",
            source="Crossref",
        )

        self.assertTrue(papers_match(first, same_doi))
        self.assertTrue(papers_match(first, no_doi))
        self.assertFalse(papers_match(first, conflicting_doi))
        unique = deduplicate_papers([first, same_doi, no_doi, conflicting_doi])
        self.assertEqual(len(unique), 2)
        merged = next(paper for paper in unique if paper.doi == "10.1000/abc")
        self.assertEqual(merged.record_id, first.record_id)
        self.assertEqual(set(merged.sources), {"Crossref", "OpenAlex", "Zotero"})
        self.assertEqual(merged.evidence_level, EvidenceLevel.ABSTRACT_ONLY)

    def test_catalog_and_bibtex_expose_required_fields_and_labels(self) -> None:
        paper = PaperRecord(
            title="Evidence & practice",
            authors=["Smith, Jane"],
            year=2024,
            doi="10.1000/test",
            url="https://example.test/full",
            source="OpenAlex",
            journal="Journal of Tests",
            evidence_level=EvidenceLevel.ABSTRACT_ONLY,
        )

        catalog = render_literature_catalog([paper], topic="Test topic")
        bibtex = render_bibtex([paper])

        self.assertIn("10.1000/test", catalog)
        self.assertIn("[打开](https://example.test/full)", catalog)
        self.assertIn("仅摘要证据", catalog)
        self.assertIn("@article{smith2024", bibtex)
        self.assertIn(r"Evidence \& practice", bibtex)


class AdapterAndRunTests(unittest.TestCase):
    def test_zotero_attachment_is_not_promoted_to_validated_full_text(self) -> None:
        class Connector:
            def search_preview(self, query: str, *, limit: int = 5) -> list[SearchPreviewItem]:
                return [
                    SearchPreviewItem(
                        source="Zotero",
                        title="Local item",
                        creators=["Ada Lovelace"],
                        year="2024",
                        evidence_level="本地全文候选",
                    )
                ]

        paper = ZoteroSearchProvider(Connector()).search("local")[0]
        self.assertEqual(paper.evidence_level, EvidenceLevel.METADATA_ONLY)

    def test_ima_result_is_explicit_knowledge_snippet(self) -> None:
        class Connector:
            def search_preview(self, query: str, *, limit: int = 5) -> list[SearchPreviewItem]:
                return [SearchPreviewItem(source="IMA", title="KB item", snippet="Matched text")]

        paper = ImaSearchProvider(Connector()).search("matched")[0]
        self.assertEqual(paper.evidence_level, EvidenceLevel.KNOWLEDGE_SNIPPET)

    def test_search_all_continues_after_one_provider_failure(self) -> None:
        class Good:
            name = "Good"

            def search(self, query: str, **kwargs: object) -> list[PaperRecord]:
                return [PaperRecord(title="Good result", source=self.name, queries=[query])]

        class Bad:
            name = "Bad"

            def search(self, query: str, **kwargs: object) -> list[PaperRecord]:
                raise SearchProviderError("quota exceeded")

        result = search_all([Good(), Bad()], ["test query"], max_workers=2)  # type: ignore[list-item]

        self.assertEqual(len(result.papers), 1)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.failures[0].provider, "Bad")
        self.assertIn("quota", result.failures[0].message)


if __name__ == "__main__":
    unittest.main()
