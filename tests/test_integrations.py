import unittest
from unittest.mock import patch

from review_writer.integrations.base import IntegrationError, SearchPreviewItem
from review_writer.integrations.ima import ImaConnector
from review_writer.integrations.library import LibraryConnector, infer_access_route
from review_writer.integrations.zotero import ZoteroConnector
from review_writer.settings import ImaSettings, LibrarySettings, ZoteroSettings


class LibraryConnectorTests(unittest.TestCase):
    def test_infers_common_authorization_routes(self) -> None:
        cases = {
            "https://lib.example.edu/authserver/login": "CAS / SSO",
            "https://ds.carsi.edu.cn/idp/profile": "CARSI / Shibboleth",
            "https://ezproxy.example.edu/login": "EZproxy",
            "https://webvpn.example.edu/": "WebVPN",
            "https://example.metaersp.cn/": "图书馆资源聚合门户",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(infer_access_route(url), expected)

    def test_missing_portal_is_explicit_configuration_gate(self) -> None:
        result = LibraryConnector(LibrarySettings()).check()

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "config_required")


class ZoteroConnectorTests(unittest.TestCase):
    @patch.object(ZoteroConnector, "_run_helper")
    def test_status_reports_running_local_api(self, helper) -> None:
        helper.return_value = {
            "api_running": True,
            "connector_running": True,
            "zotero_version": "9.0.6",
        }

        result = ZoteroConnector(ZoteroSettings()).check()

        self.assertTrue(result.ok)
        self.assertIn("9.0.6", result.message)

    @patch.object(ZoteroConnector, "_api_get")
    @patch.object(ZoteroConnector, "_run_helper")
    def test_status_falls_back_to_direct_local_api(self, helper, api_get) -> None:
        helper.side_effect = IntegrationError("helper unavailable")
        api_get.return_value = []

        result = ZoteroConnector(ZoteroSettings()).check()

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ready")
        api_get.assert_called_once_with("users/0/items?limit=1")

    @patch.object(ZoteroConnector, "_api_get")
    @patch.object(ZoteroConnector, "_run_helper")
    def test_search_normalizes_doi_and_attachment_status(self, helper, api_get) -> None:
        helper.return_value = [
            {
                "key": "ITEMKEY1",
                "title": "A useful paper",
                "creators": ["Alice Example"],
                "year": "2025",
                "bibtexKey": "example2025",
            }
        ]
        api_get.side_effect = [
            {"data": {"title": "A useful paper", "DOI": "10.1234/example", "url": "https://doi.org/10.1234/example", "tags": [], "collections": []}},
            [{"data": {"itemType": "attachment"}}],
        ]

        results = ZoteroConnector(ZoteroSettings()).search_preview("useful")

        self.assertEqual(results[0].doi, "10.1234/example")
        self.assertEqual(results[0].citation_key, "example2025")
        self.assertEqual(results[0].evidence_level, "本地全文候选")

    @patch.object(ZoteroConnector, "_api_get")
    @patch.object(ZoteroConnector, "_run_helper")
    def test_search_falls_back_to_direct_local_api(self, helper, api_get) -> None:
        helper.side_effect = IntegrationError("helper unavailable")
        item = {
            "key": "ITEMKEY2",
            "data": {
                "itemType": "journalArticle",
                "title": "Direct API paper",
                "date": "2026-01-02",
                "creators": [{"firstName": "Bo", "lastName": "Chen"}],
                "DOI": "10.1234/direct",
                "url": "https://doi.org/10.1234/direct",
                "tags": [],
                "collections": [],
            },
        }
        api_get.side_effect = [[item], item, []]

        results = ZoteroConnector(ZoteroSettings()).search_preview("direct")

        self.assertEqual(results[0].title, "Direct API paper")
        self.assertEqual(results[0].creators, ["Bo Chen"])
        self.assertEqual(results[0].year, "2026")


class ImaConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connector = ImaConnector(
            ImaSettings(knowledge_base_id="kb-visible", knowledge_base_name="共享库"),
            client_id="client",
            api_key="key",
        )

    @patch.object(ImaConnector, "_call")
    def test_lists_visible_knowledge_bases_without_exposing_ids_in_message(self, call) -> None:
        call.return_value = {
            "info_list": [{"id": "kb-visible", "name": "共享库"}],
            "is_end": True,
        }

        result = self.connector.check()

        self.assertTrue(result.ok)
        self.assertNotIn("kb-visible", result.message)
        self.assertEqual(result.details["knowledge_bases"][0]["name"], "共享库")

    @patch.object(ImaConnector, "_call")
    def test_search_labels_snippet_evidence_when_no_media_url(self, call) -> None:
        call.side_effect = [
            {"info_list": [{"media_id": "media-secret", "title": "知识条目", "highlight_content": "命中片段"}]},
            {"media_type": 1},
        ]

        results = self.connector.search_preview("关键词")

        self.assertEqual(results[0].evidence_level, "IMA 知识库片段证据")
        self.assertNotIn("media-secret", results[0].display_text())


if __name__ == "__main__":
    unittest.main()
