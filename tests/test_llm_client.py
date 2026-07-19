import unittest
from unittest.mock import patch

from review_writer.generators.llm_client import LLMClient, LLMRequestError
from review_writer.settings import ModelSettings


class LLMClientTests(unittest.TestCase):
    @patch("review_writer.generators.llm_client._request_json")
    def test_openai_responses_uses_json_object_text_format(self, request_json) -> None:
        request_json.return_value = {"output_text": '{"ok": true}'}
        settings = ModelSettings(
            model="gpt-5.6",
            protocol="openai_responses",
            api_base="https://api.openai.com/v1",
        )

        text = LLMClient(settings, "secret").request_text(
            system_prompt="system",
            user_prompt="user",
            json_mode=True,
        )

        self.assertEqual(text, '{"ok": true}')
        payload = request_json.call_args.kwargs["payload"]
        self.assertEqual(payload["text"]["format"]["type"], "json_object")
        self.assertEqual(payload["instructions"], "system")
        self.assertNotIn("temperature", payload)

    def test_missing_key_is_rejected_except_for_ollama(self) -> None:
        with self.assertRaisesRegex(LLMRequestError, "配置不完整"):
            LLMClient(ModelSettings(protocol="openai_compatible"), None)

        client = LLMClient(
            ModelSettings(protocol="ollama", api_base="http://localhost:11434", model="qwen3:8b"),
            None,
        )
        self.assertEqual(client.settings.protocol, "ollama")


if __name__ == "__main__":
    unittest.main()
