import json
from pathlib import Path
import tempfile
import unittest

from review_writer.model_catalog import load_model_catalog
from review_writer.settings import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip_preserves_nested_settings_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            store = SettingsStore(path)
            settings = AppSettings()
            settings.model.provider_name = "测试服务商"
            settings.model.model = "test-model"
            settings.model.pricing_mode = "manual"
            settings.model.cached_input_price_per_million = 0.25
            settings.model.price_tiers = [{"max_input_tokens": 100000, "input_price_per_million": 1}]
            settings.model_catalog.update_url = "https://catalog.example.test/models.json"
            settings.model_catalog.update_interval_days = 14
            settings.discovery.polite_email = "researcher@example.org"
            settings.ima.knowledge_base_name = "共享知识库"
            settings.appearance.theme_id = "forest"
            settings.appearance.ui_font = "Segoe UI"
            settings.appearance.mono_font = "Consolas"

            store.save(settings)
            loaded = store.load()

            self.assertEqual(loaded.model.model, "test-model")
            self.assertEqual(loaded.model.pricing_mode, "manual")
            self.assertEqual(loaded.model.cached_input_price_per_million, 0.25)
            self.assertEqual(loaded.model.price_tiers[0]["max_input_tokens"], 100000)
            self.assertEqual(loaded.model_catalog.update_interval_days, 14)
            self.assertEqual(loaded.discovery.polite_email, "researcher@example.org")
            self.assertEqual(loaded.ima.knowledge_base_name, "共享知识库")
            self.assertEqual(loaded.appearance.theme_id, "forest")
            self.assertEqual(loaded.appearance.ui_font, "Segoe UI")
            self.assertEqual(loaded.appearance.mono_font, "Consolas")
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            self.assertNotIn("api_key", payload["model"])
            self.assertNotIn("api_key", payload["ima"])
            self.assertNotIn("client_id", payload["ima"])

    def test_corrupt_file_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text("not json", encoding="utf-8")

            settings = SettingsStore(path).load()

            self.assertEqual(settings.model.provider_id, "openai")

    def test_vertical_windows_font_alias_is_safely_discarded(self) -> None:
        settings = AppSettings.from_dict(
            {"appearance": {"ui_font": "@微软雅黑", "mono_font": "@宋体"}}
        )

        self.assertEqual(settings.appearance.ui_font, "")
        self.assertEqual(settings.appearance.mono_font, "")


class ModelCatalogTests(unittest.TestCase):
    def test_catalog_has_eight_verified_provider_recommendations(self) -> None:
        catalog = load_model_catalog()

        self.assertEqual(len(catalog), 8)
        self.assertEqual(
            {entry.provider_id for entry in catalog},
            {"openai", "anthropic", "google", "deepseek", "qwen", "kimi", "zhipu", "ollama"},
        )
        for entry in catalog:
            self.assertTrue(entry.api_base.startswith(("http://", "https://")))
            self.assertTrue(entry.model_url.startswith("https://"))
            self.assertTrue(entry.pricing_url.startswith("https://"))
            self.assertEqual(entry.last_verified, "2026-07-12")
        openai = next(entry for entry in catalog if entry.provider_id == "openai")
        self.assertEqual(openai.input_price_per_million, 5)
        self.assertEqual(openai.output_price_per_million, 30)
        self.assertTrue(openai.capability_tags)


if __name__ == "__main__":
    unittest.main()
