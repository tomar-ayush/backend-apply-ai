import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel

from app.common.exceptions import BadRequestError, ExternalServiceError
from app.llm.client import LLMClient


class SampleSchema(BaseModel):
    name: str
    count: int


def test_llm_client_unsupported_provider():
    with pytest.raises(BadRequestError, match="Unsupported LLM provider"):
        LLMClient(provider="unsupported_llm", api_key="secret")


def test_llm_client_provider_aliases():
    with patch("app.llm.client.LLMClient._build_client", return_value=MagicMock()):
        c1 = LLMClient(provider="claude", api_key="k1")
        assert c1.provider == "anthropic"

        c2 = LLMClient(provider="google", api_key="k2")
        assert c2.provider == "gemini"


@pytest.mark.asyncio
async def test_llm_client_complete_json_success():
    mock_client = MagicMock()
    with patch("app.llm.client.LLMClient._build_client", return_value=mock_client):
        client = LLMClient(provider="openai", api_key="key")
        with patch.object(
            client, "complete", return_value='{"name": "test", "count": 42}'
        ):
            result = await client.complete_json(
                system="sys",
                user="usr",
                response_schema=SampleSchema,
            )
            assert result == {"name": "test", "count": 42}


@pytest.mark.asyncio
async def test_llm_client_complete_json_retry_on_invalid_json():
    mock_client = MagicMock()
    with patch("app.llm.client.LLMClient._build_client", return_value=mock_client):
        client = LLMClient(provider="openai", api_key="key")

        # First return invalid JSON, second call returns valid JSON
        responses = ['{"name": "incomplete', '{"name": "repaired", "count": 10}']

        with patch.object(client, "complete", side_effect=responses):
            result = await client.complete_json(
                system="sys",
                user="usr",
                response_schema=SampleSchema,
                max_retries=1,
            )
            assert result == {"name": "repaired", "count": 10}


@pytest.mark.asyncio
async def test_llm_client_complete_json_retry_exhausted_raises():
    mock_client = MagicMock()
    with patch("app.llm.client.LLMClient._build_client", return_value=mock_client):
        client = LLMClient(provider="openai", api_key="key")

        with patch.object(client, "complete", return_value="invalid json"):
            with pytest.raises(ExternalServiceError, match="Response was not structurally valid"):
                await client.complete_json(
                    system="sys",
                    user="usr",
                    response_schema=SampleSchema,
                    max_retries=0,
                )


@pytest.mark.asyncio
async def test_llm_client_openrouter_complete_json():
    mock_client = MagicMock()
    with patch("app.llm.client.LLMClient._build_client", return_value=mock_client):
        client = LLMClient(provider="openrouter", api_key="sk-or-key")
        with patch.object(
            client, "complete", return_value='{"name": "openrouter", "count": 100}'
        ) as mock_complete:
            result = await client.complete_json(
                system="sys",
                user="usr",
                model="openai/gpt-4o-mini",
                response_schema=SampleSchema,
            )
            assert result == {"name": "openrouter", "count": 100}
            assert mock_complete.called
            call_kwargs = mock_complete.call_args[1]
            assert call_kwargs["json_mode"] is True
            assert "JSON Schema" in call_kwargs["system"]
