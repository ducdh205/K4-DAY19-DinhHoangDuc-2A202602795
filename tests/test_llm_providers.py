"""Offline provider contracts; no keys, API calls, or model downloads."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import llm


class TestGroqAndLocal(unittest.TestCase):
    def test_transient_connection_failure_is_retried(self):
        APIConnectionError = type("APIConnectionError", (Exception,), {})
        model = llm.MeteredLLM.__new__(llm.MeteredLLM)
        model.chat_provider = "groq"
        create = unittest.mock.Mock(side_effect=[APIConnectionError(), "ok"])
        model._chat_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm.time, "sleep") as sleep:
            self.assertEqual(model._create_chat_completion(model="openai/gpt-oss-20b"), "ok")
            sleep.assert_called_once()

    def test_groq_rate_limit_retries_with_server_delay(self):
        class RateLimited(Exception):
            status_code = 429
            response = SimpleNamespace(headers={"retry-after": "2"})
        model = llm.MeteredLLM.__new__(llm.MeteredLLM)
        model.chat_provider = "groq"
        create = unittest.mock.Mock(side_effect=[RateLimited(), "ok"])
        model._chat_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm.time, "sleep") as sleep:
            self.assertEqual(model._create_chat_completion(model="openai/gpt-oss-20b"), "ok")
            self.assertGreaterEqual(sleep.call_args.args[0], 2)
        self.assertEqual(create.call_count, 2)

    def test_non_rate_limit_error_is_not_retried(self):
        model = llm.MeteredLLM.__new__(llm.MeteredLLM)
        model.chat_provider = "groq"
        create = unittest.mock.Mock(side_effect=ValueError("invalid request"))
        model._chat_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.object(llm.time, "sleep") as sleep, self.assertRaises(ValueError):
            model._create_chat_completion(model="openai/gpt-oss-20b")
        sleep.assert_not_called()

    def test_groq_chat_not_an_embedding_provider(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}, clear=True):
            self.assertEqual(llm.pick_provider("LLM_PROVIDER", False), "groq")
            with self.assertRaisesRegex(RuntimeError, "embedding"):
                llm.pick_provider("EMBEDDING_PROVIDER", True)

    def test_local_is_explicit_and_needs_no_key(self):
        with patch.dict(os.environ, {"EMBEDDING_PROVIDER": "local"}, clear=True):
            self.assertEqual(llm.pick_provider("EMBEDDING_PROVIDER", True), "local")
            with self.assertRaises(RuntimeError):
                llm.pick_provider("LLM_PROVIDER", False)

    def test_groq_prices_and_json_usage(self):
        requests = []
        def create(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"cases": []}'))],
                                   usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with patch.dict(os.environ, {}, clear=True), patch.object(llm, "_openai_client", return_value=client):
            model = llm.MeteredLLM(chat_provider="groq", embed_provider="local")
            self.assertEqual(model.chat("Return JSON", json_mode=True), '{"cases": []}')
        self.assertEqual(requests[0]["model"], "openai/gpt-oss-20b")
        self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
        self.assertEqual(requests[0]["max_completion_tokens"], 4096)
        model.chat("Answer briefly")
        self.assertEqual(requests[1]["max_completion_tokens"], 1536)
        self.assertEqual(model.usage.calls, 2)
        self.assertAlmostEqual(model.usage.usd, 2 * (100 * .075 + 50 * .30) / 1_000_000)

    def test_local_embeddings_are_normalized_and_metered_without_api_cost(self):
        class LocalModel:
            def embed(self, texts):
                return iter([[3, 4]])
        with patch.dict(os.environ, {}, clear=True), patch.object(llm, "_openai_client"):
            model = llm.MeteredLLM(chat_provider="groq", embed_provider="local")
            model._embed_client = LocalModel()
            self.assertEqual(model.embed("Tiền chất là gì?"), [.6, .8])
        self.assertEqual(model.usage.calls, 1)
        self.assertEqual(model.usage.usd, 0)
        self.assertEqual(model.usage.input_tokens, 0)
