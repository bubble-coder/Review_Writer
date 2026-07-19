from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.error import HTTPError

from review_writer.llm_policy import MaterialDescriptor, ModelCallPolicy, preflight_model_call, DataClass
from review_writer.model_catalog import ModelCatalogService, load_model_catalog_document
from review_writer.settings import ModelSettings


FIXTURE = Path(__file__).with_name("fixtures") / "model_catalog_update.json"


class _Response:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = BytesIO(payload)
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class ModelCatalogUpdateTests(unittest.TestCase):
    def test_local_import_is_atomic_and_invalid_followup_keeps_last_good(self) -> None:
        with TemporaryDirectory() as directory:
            service = ModelCatalogService(Path(directory))
            result = service.import_file(FIXTURE)
            self.assertEqual(result.document.catalog_version, "test-2")
            self.assertEqual(result.document.models[0].input_price_per_million, 1)

            broken = Path(directory) / "broken.json"
            broken.write_text('{"schema_version": 2, "models": []}', encoding="utf-8")
            with self.assertRaises(ValueError):
                service.import_file(broken)

            effective = service.load_effective()
            self.assertEqual(effective.catalog_version, "test-2")
            self.assertEqual(effective.source, "本地文件：model_catalog_update.json")

    def test_remote_refresh_uses_conditional_headers_and_handles_304(self) -> None:
        requests = []
        payload = FIXTURE.read_bytes()

        def first_opener(request, timeout):
            requests.append(request)
            return _Response(payload, {"ETag": '"catalog-v2"', "Last-Modified": "Mon, 13 Jul 2026 00:00:00 GMT"})

        with TemporaryDirectory() as directory:
            service = ModelCatalogService(Path(directory), opener=first_opener)
            result = service.update_from_url("https://catalog.example.test/models.json")
            self.assertEqual(result.status, "updated")
            self.assertEqual(service.state()["etag"], '"catalog-v2"')

            def not_modified(request, timeout):
                requests.append(request)
                raise HTTPError(request.full_url, 304, "Not Modified", {}, None)

            service.opener = not_modified
            unchanged = service.update_from_url("https://catalog.example.test/models.json")
            self.assertEqual(unchanged.status, "not_modified")
            self.assertEqual(requests[-1].get_header("If-none-match"), '"catalog-v2"')

    def test_remote_refresh_rejects_non_https_and_oversized_or_invalid_data(self) -> None:
        with TemporaryDirectory() as directory:
            service = ModelCatalogService(Path(directory), opener=lambda *_args, **_kwargs: _Response(b"not-json"))
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                service.update_from_url("http://catalog.example.test/models.json")
            with self.assertRaisesRegex(ValueError, "UTF-8 JSON"):
                service.update_from_url("https://catalog.example.test/models.json")
            self.assertIn("UTF-8 JSON", service.state()["last_error"])

    def test_schema_one_catalog_remains_readable(self) -> None:
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["schema_version"] = 1
        raw.pop("catalog_version")
        raw.pop("updated_at")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            document = load_model_catalog_document(path)
            self.assertEqual(document.schema_version, 1)
            self.assertEqual(document.models[0].model, "example-research-2")

    def test_tiered_price_is_selected_from_input_size(self) -> None:
        settings = ModelSettings(max_output_tokens=1000)
        policy = ModelCallPolicy(
            input_price_per_million=1,
            output_price_per_million=4,
            price_tiers=[
                {"min_input_tokens": 0, "max_input_tokens": 100, "input_price_per_million": 1, "output_price_per_million": 4},
                {"min_input_tokens": 101, "input_price_per_million": 2, "output_price_per_million": 8},
            ],
        )
        result = preflight_model_call(
            settings,
            policy,
            [MaterialDescriptor("m", DataClass.ABSTRACT, 500)],
            purpose="reading",
        )
        self.assertEqual(result.estimated_input_tokens, 200)
        self.assertEqual(result.estimated_cost, 0.0084)


if __name__ == "__main__":
    unittest.main()
