from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from revenueguard_agents import (
    ModelProviderError,
    OpenAICompatibleStructuredModel,
    ProviderHttpResponse,
    StructuredResponseMode,
    TokenLimitField,
)
from revenueguard_agents.contracts import DiagnosisOutput


class CapturingTransport:
    def __init__(self, response: ProviderHttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str], bytes, float]] = []

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        self.calls.append((url, headers, body, timeout_seconds))
        return self.response


def _provider_response(output: Mapping[str, object]) -> ProviderHttpResponse:
    return ProviderHttpResponse(
        status_code=200,
        body=json.dumps(
            {
                "model": "provider-model-revision",
                "choices": [{"message": {"role": "assistant", "content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 31, "completion_tokens": 17},
            }
        ).encode(),
    )


async def test_openai_compatible_adapter_sends_strict_schema_and_bearer_auth() -> None:
    transport = CapturingTransport(
        _provider_response(
            {
                "diagnosis_code": "EXPIRED_PAYMENT_METHOD",
                "confidence_basis_points": 9000,
                "rationale": "Evidence supports the existing diagnosis.",
            }
        )
    )
    model = OpenAICompatibleStructuredModel(
        base_url="https://api.openai.com/v1/",
        model_name="pinned-model-id",
        api_key="secret-value",
        transport=transport,
        timeout_seconds=3,
    )

    response = await model.generate(
        node="DIAGNOSIS_ASSISTANCE",
        payload={"evidence": ["permitted"]},
        response_schema=DiagnosisOutput,
        max_output_tokens=256,
    )

    assert response.payload["diagnosis_code"] == "EXPIRED_PAYMENT_METHOD"
    assert (response.input_tokens, response.output_tokens) == (31, 17)
    assert model.model_version == "openai-compatible:pinned-model-id"
    url, headers, raw_body, timeout = transport.calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret-value"
    assert timeout == 3
    body = json.loads(raw_body)
    assert body["model"] == "pinned-model-id"
    assert body["max_completion_tokens"] == 256
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert "secret-value" not in raw_body.decode()


async def test_local_server_supports_json_object_mode_without_api_key() -> None:
    transport = CapturingTransport(
        _provider_response(
            {
                "diagnosis_code": "EXPIRED_PAYMENT_METHOD",
                "confidence_basis_points": 8500,
                "rationale": "Local structured response.",
            }
        )
    )
    model = OpenAICompatibleStructuredModel(
        base_url="http://127.0.0.1:11434/v1",
        model_name="local-model",
        response_mode=StructuredResponseMode.JSON_OBJECT,
        token_limit_field=TokenLimitField.MAX_TOKENS,
        transport=transport,
    )

    await model.generate(
        node="DIAGNOSIS_ASSISTANCE",
        payload={"evidence": []},
        response_schema=DiagnosisOutput,
        max_output_tokens=128,
    )

    url, headers, raw_body, _ = transport.calls[0]
    body = json.loads(raw_body)
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
    assert "Authorization" not in headers
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 128
    assert "max_completion_tokens" not in body


async def test_provider_errors_are_sanitized_and_do_not_expose_response_body() -> None:
    transport = CapturingTransport(
        ProviderHttpResponse(status_code=401, body=b'{"error":"prompt or secret echoed"}')
    )
    model = OpenAICompatibleStructuredModel(
        base_url="https://provider.example/v1",
        model_name="model",
        api_key="secret-value",
        transport=transport,
    )

    with pytest.raises(ModelProviderError, match="HTTP 401") as captured:
        await model.generate(
            node="DIAGNOSIS_ASSISTANCE",
            payload={},
            response_schema=DiagnosisOutput,
            max_output_tokens=128,
        )

    assert "secret" not in str(captured.value)
    assert "prompt" not in str(captured.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "provider.example/v1",
        "ftp://provider.example/v1",
        "https://user:password@provider.example/v1",
        "https://provider.example/v1?tenant=other",
        "https://provider.example/v1#fragment",
        "http://provider.example/v1",
    ],
)
def test_provider_rejects_unsafe_or_ambiguous_base_urls(base_url: str) -> None:
    with pytest.raises(ValueError, match="base URL"):
        OpenAICompatibleStructuredModel(base_url=base_url, model_name="model")
