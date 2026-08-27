"""OpenAI-compatible structured-model adapter for cloud and local providers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel

from revenueguard_agents.contracts import ModelResponse

_MAX_RESPONSE_BYTES = 1_048_576
_SYSTEM_INSTRUCTION = (
    "You are RevenueGuard's read-only advisory model. Use only the supplied evidence. "
    "Return only the requested JSON object. Never invent provider state, consent, history, "
    "policy, or evidence. You cannot execute payments or contact customers; deterministic "
    "policy makes every authorization decision."
)


class StructuredResponseMode(StrEnum):
    """Structured-output dialect exposed by an OpenAI-compatible server."""

    JSON_SCHEMA = "JSON_SCHEMA"
    JSON_OBJECT = "JSON_OBJECT"


class TokenLimitField(StrEnum):
    """Token-limit field supported by the configured compatibility server."""

    MAX_COMPLETION_TOKENS = "MAX_COMPLETION_TOKENS"
    MAX_TOKENS = "MAX_TOKENS"


class ModelProviderError(RuntimeError):
    """A sanitized provider failure safe to collapse into deterministic fallback."""


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    status_code: int
    body: bytes


class ProviderHttpTransport(Protocol):
    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse: ...


class UrllibProviderHttpTransport:
    """Small dependency-free HTTP transport with bounded response reads."""

    async def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        return await asyncio.to_thread(
            self._post_json,
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _post_json(
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> ProviderHttpResponse:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                response_body = response.read(_MAX_RESPONSE_BYTES + 1)
                status_code = response.status
        except HTTPError as error:
            response_body = error.read(_MAX_RESPONSE_BYTES + 1)
            status_code = error.code
        except (TimeoutError, URLError) as error:
            raise ModelProviderError("model provider transport failed") from error
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise ModelProviderError("model provider response exceeded size limit")
        return ProviderHttpResponse(status_code=status_code, body=response_body)


class OpenAICompatibleStructuredModel:
    """Call a configured `/v1/chat/completions` compatible model server.

    The base URL is operator configuration, never model or request input. The adapter has no
    business-action methods and returns only a JSON mapping for graph-level validation.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        api_key: str | None = None,
        response_mode: StructuredResponseMode = StructuredResponseMode.JSON_SCHEMA,
        token_limit_field: TokenLimitField = TokenLimitField.MAX_COMPLETION_TOKENS,
        timeout_seconds: float = 10,
        transport: ProviderHttpTransport | None = None,
    ) -> None:
        if not model_name.strip():
            raise ValueError("model_name is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        normalized_base_url = _normalize_base_url(base_url)
        self._endpoint = f"{normalized_base_url}/chat/completions"
        self._model_name = model_name.strip()
        self._response_mode = StructuredResponseMode(response_mode)
        self._token_limit_field = TokenLimitField(token_limit_field)
        self._timeout_seconds = timeout_seconds
        self._transport = transport or UrllibProviderHttpTransport()
        self._headers = {"Content-Type": "application/json"}
        if api_key is not None and api_key.strip():
            self._headers["Authorization"] = f"Bearer {api_key.strip()}"

    @property
    def model_version(self) -> str:
        return f"openai-compatible:{self._model_name}"

    async def generate(
        self,
        *,
        node: str,
        payload: Mapping[str, object],
        response_schema: type[BaseModel],
        max_output_tokens: int,
    ) -> ModelResponse:
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        request_document: dict[str, object] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": _SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"node": node, "input": payload},
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                },
            ],
            "response_format": self._response_format(response_schema),
            self._token_field_name: max_output_tokens,
        }
        response = await self._transport.post_json(
            url=self._endpoint,
            headers=self._headers,
            body=json.dumps(request_document, separators=(",", ":")).encode(),
            timeout_seconds=self._timeout_seconds,
        )
        if not 200 <= response.status_code < 300:
            # Provider bodies can echo prompts or internal details, so never include them.
            raise ModelProviderError(f"model provider returned HTTP {response.status_code}")
        document = _json_mapping(response.body, description="provider response")
        content = _message_content(document)
        output = _json_mapping(content.encode(), description="model output")
        input_tokens, output_tokens = _usage(document)
        return ModelResponse(
            payload=output,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @property
    def _token_field_name(self) -> str:
        if self._token_limit_field is TokenLimitField.MAX_TOKENS:
            return "max_tokens"
        return "max_completion_tokens"

    def _response_format(self, response_schema: type[BaseModel]) -> Mapping[str, object]:
        if self._response_mode is StructuredResponseMode.JSON_OBJECT:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_schema.__name__,
                "strict": True,
                "schema": response_schema.model_json_schema(),
            },
        }


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("model base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("model base URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("model base URL cannot contain a query or fragment")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ValueError("HTTP model base URLs are allowed only for loopback local servers")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _is_loopback_host(hostname: str) -> bool:
    if hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _json_mapping(value: bytes, *, description: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelProviderError(f"{description} was not valid JSON") from error
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ModelProviderError(f"{description} must be a JSON object")
    return parsed


def _message_content(document: Mapping[str, object]) -> str:
    choices = document.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelProviderError("provider response did not contain a choice")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ModelProviderError("provider choice was malformed")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ModelProviderError("provider response did not contain a message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ModelProviderError("provider message did not contain JSON content")
    return content


def _usage(document: Mapping[str, object]) -> tuple[int, int]:
    usage = document.get("usage")
    if not isinstance(usage, Mapping):
        return 0, 0
    return _non_negative_int(usage.get("prompt_tokens")), _non_negative_int(
        usage.get("completion_tokens")
    )


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value
