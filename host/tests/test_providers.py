"""AI Provider 단위 테스트 — 실제 API 호출 없이."""
import json, pytest
from ai.providers.base import AITier
from ai.providers.factory import create_provider, list_providers


class TestProviderRegistry:
    def test_all_providers_registered(self):
        providers = list_providers()
        for name in ['anthropic','openai','google','ollama',
                     'gemini_cli','codex_cli']:
            assert name in providers, f"'{name}' 미등록"

    def test_create_gemini_cli(self):
        p = create_provider('gemini_cli', api_key='test')
        assert p.name == 'gemini_cli'

    def test_create_codex_cli(self):
        p = create_provider('codex_cli', api_key='test')
        assert p.name == 'codex_cli'


class TestGeminiCLIProvider:
    def setup_method(self):
        from ai.providers.gemini_cli_provider import GeminiCLIProvider
        self.p = GeminiCLIProvider(api_key='test', timeout=10)

    def test_name(self):
        assert self.p.name == 'gemini_cli'

    def test_model_tiers(self):
        assert 'gemini' in self.p.model_for_tier(AITier.TIER1)
        assert 'gemini' in self.p.model_for_tier(AITier.TIER2)

    def test_estimate_cost_positive(self):
        cost = self.p.estimate_cost(1000, 500, AITier.TIER1)
        assert isinstance(cost, float) and cost >= 0

    def test_parse_json_output(self):
        raw = json.dumps({
            "response": "stack_overflow 감지",
            "stats": {"input_tokens": 50, "output_tokens": 200}
        })
        text, ti, to = self.p._parse_output(raw)
        assert 'stack_overflow' in text
        assert ti == 50 and to == 200

    def test_parse_error_response(self):
        raw = json.dumps({"error": "Rate limit exceeded"})
        text, _, _ = self.p._parse_output(raw)
        assert 'Rate limit' in text

    def test_parse_text_fallback(self):
        text, ti, to = self.p._parse_output("단순 텍스트")
        assert text == "단순 텍스트"
        assert ti == 0 and to == 0

    def test_parse_empty(self):
        text, _, _ = self.p._parse_output("")
        assert text == ""

    def test_parse_jsonl_fallback(self):
        jsonl = ('{"type":"message","role":"assistant","content":"분석 완료"}\n'
                 '{"type":"result","stats":{"input_tokens":30,"output_tokens":100}}')
        text, ti, to = self.p._parse_output(jsonl)
        assert '분석' in text and ti == 30


class TestCodexCLIProvider:
    def setup_method(self):
        from ai.providers.codex_cli_provider import CodexCLIProvider
        self.p = CodexCLIProvider(api_key='test', timeout=10)

    def test_name(self):
        assert self.p.name == 'codex_cli'

    def test_model_tiers(self):
        assert 'gpt' in self.p.model_for_tier(AITier.TIER1) or \
               'codex' in self.p.model_for_tier(AITier.TIER1)

    def test_parse_jsonl(self):
        jsonl = (
            '{"type":"agent_message","content":"stack_overflow 분석"}\n'
            '{"type":"session_info","usage":{"input_tokens":100,"output_tokens":300}}'
        )
        text, ti, to = self.p._parse_output(jsonl)
        assert 'stack_overflow' in text
        assert ti == 100 and to == 300

    def test_parse_reasoning_ignored(self):
        jsonl = (
            '{"type":"reasoning","content":"내부 추론 내용"}\n'
            '{"type":"agent_message","content":"최종 결론"}'
        )
        text, _, _ = self.p._parse_output(jsonl)
        assert '내부 추론' not in text
        assert '최종 결론' in text

    def test_parse_empty(self):
        text, _, _ = self.p._parse_output("")
        assert text == ""

    def test_is_available_bool(self):
        assert isinstance(self.p.is_available(), bool)

    def test_generate_without_cli_returns_fallback(self):
        """CLI 없을 때 AIResponse 반환 (예외 아님)."""
        if self.p.is_available():
            pytest.skip("codex CLI 설치됨 — 실제 호출 스킵")
        from ai.providers.base import AIResponse
        resp = self.p.generate("system", "user", 512, AITier.TIER1)
        assert isinstance(resp, AIResponse)
        assert '설치' in resp.text or 'CLI' in resp.text
