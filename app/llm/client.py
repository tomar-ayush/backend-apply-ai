import json
from app.common.logging import get_logger
from typing import Any, Dict, Optional

from app.common.exceptions import ExternalServiceError, BadRequestError

logger = get_logger(__name__)


class LLMClient:
    def __init__(self, provider: str, api_key: str):
        self.provider = provider.lower()
        self.api_key = api_key
        self._client = self._build_client()

    def _build_client(self):
        if self.provider == "openai":
            try:
                from openai import AsyncOpenAI
                return AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                raise BadRequestError("openai package not installed")
        elif self.provider in ("anthropic", "claude"):
            try:
                import anthropic
                return anthropic.AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise BadRequestError("anthropic package not installed")
        else:
            raise BadRequestError(f"Unsupported LLM provider: {self.provider}")

    async def complete(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> str:
        try:
            if self.provider == "openai":
                return await self._openai_complete(system, user, model, max_tokens)
            else:
                return await self._anthropic_complete(system, user, model, max_tokens)
        except Exception as e:
            logger.error("llm_error provider=%s error=%s", self.provider, str(e))
            raise ExternalServiceError("LLM", str(e))

    async def _openai_complete(self, system: str, user: str, model: Optional[str], max_tokens: int) -> str:
        model = model or "gpt-4o"
        response = await self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def _anthropic_complete(self, system: str, user: str, model: Optional[str], max_tokens: int) -> str:
        model = model or "claude-sonnet-4-6"
        response = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    async def complete_json(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        raw = await self.complete(system, user, model, max_tokens)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("llm_json_parse_error raw=%s error=%s", raw[:200], str(e))
            raise ExternalServiceError("LLM", f"Response was not valid JSON: {str(e)}")
