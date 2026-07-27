import json
import re
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError

from app.common.exceptions import BadRequestError, ExternalServiceError
from app.common.logging import get_logger

logger = get_logger(__name__)

PROVIDER_ALIASES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    "openrouter": "openrouter",
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient:
    def __init__(self, provider: str, api_key: str):
        self.provider = PROVIDER_ALIASES.get(provider.lower())
        if self.provider is None:
            raise BadRequestError(
                f"Unsupported LLM provider: '{provider}'. "
                f"Supported: openai, anthropic, gemini, openrouter"
            )
        self.api_key = api_key
        self._client = self._build_client()

    def _build_client(self) -> Any:
        if self.provider in ("openai", "openrouter"):
            try:
                from openai import AsyncOpenAI

                base_url = (
                    OPENROUTER_BASE_URL
                    if self.provider == "openrouter"
                    else None
                )
                return AsyncOpenAI(
                    api_key=self.api_key, base_url=base_url
                )
            except ImportError:
                raise BadRequestError("openai package not installed")

        if self.provider == "anthropic":
            try:
                import anthropic

                return anthropic.AsyncAnthropic(api_key=self.api_key)
            except ImportError:
                raise BadRequestError("anthropic package not installed")

        if self.provider == "gemini":
            try:
                from google import genai

                return genai.Client(api_key=self.api_key)
            except ImportError:
                raise BadRequestError(
                    "google-genai package not installed"
                )

    @staticmethod
    def _sanitize_schema_for_gemini(schema: Any) -> Any:
        """
        Recursively removes 'additionalProperties' from JSON schemas
        to prevent Gemini Developer API mode rejections.
        """
        if isinstance(schema, dict):
            cleaned = {}
            for k, v in schema.items():
                if k == "additionalProperties":
                    continue
                cleaned[k] = LLMClient._sanitize_schema_for_gemini(v)
            return cleaned
        elif isinstance(schema, list):
            return [
                LLMClient._sanitize_schema_for_gemini(item)
                for item in schema
            ]
        return schema

    async def complete(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        json_mode: bool = False,
        response_schema: Optional[Type[BaseModel]] = None,
        max_tokens: int = 4096,
    ) -> str:
        """
        Base completion coordinator across all LLM providers.
        """
        try:
            if self.provider in ("openai", "openrouter"):
                if self.provider == "openrouter" and not model:
                    raise BadRequestError(
                        "OpenRouter requires an explicit model name (e.g. 'openai/gpt-4o-mini')."
                    )
                return await self._openai_complete(
                    system,
                    user,
                    model,
                    max_tokens,
                    json_mode,
                    response_schema,
                )
            if self.provider == "anthropic":
                return await self._anthropic_complete(
                    system, user, model, max_tokens, response_schema
                )
            if self.provider == "gemini":
                return await self._gemini_complete(
                    system,
                    user,
                    model,
                    max_tokens,
                    json_mode,
                    response_schema,
                )
        except (ExternalServiceError, BadRequestError):
            raise
        except Exception as e:
            logger.error(
                "llm_error provider=%s error=%s", self.provider, str(e)
            )
            raise ExternalServiceError("LLM", str(e))

    async def _openai_complete(
        self,
        system: str,
        user: str,
        model: Optional[str],
        max_tokens: int,
        json_mode: bool,
        response_schema: Optional[Type[BaseModel]],
    ) -> str:
        model_name = model or "gpt-4o"
        kwargs: dict = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }

        if response_schema:
            res = await self._client.beta.chat.completions.parse(
                response_format=response_schema, **kwargs
            )
            return res.choices[0].message.content or ""

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def _anthropic_complete(
        self,
        system: str,
        user: str,
        model: Optional[str],
        max_tokens: int,
        response_schema: Optional[Type[BaseModel]],
    ) -> str:
        model_name = model or "claude-3-5-sonnet-latest"
        kwargs: dict = {
            "model": model_name,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

        if response_schema:
            response = await self._client.beta.messages.parse(
                response_format=response_schema, **kwargs
            )
            return response.text

        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def _gemini_complete(
        self,
        system: str,
        user: str,
        model: Optional[str],
        max_tokens: int,
        json_mode: bool,
        response_schema: Optional[Type[BaseModel]],
    ) -> str:
        from google.genai import types

        model_name = model or "gemini-3.6-flash"
        config_args: dict = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
        }

        if response_schema:
            raw_schema = response_schema.model_json_schema()
            clean_schema = self._sanitize_schema_for_gemini(raw_schema)
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = clean_schema
        elif json_mode:
            config_args["response_mime_type"] = "application/json"
        else:
            config_args["response_mime_type"] = "text/plain"

        config = types.GenerateContentConfig(**config_args)
        response = await self._client.aio.models.generate_content(
            model=model_name,
            contents=user,
            config=config,
        )
        return response.text or ""

    async def complete_json(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        max_tokens: int = 8192,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """
        Executes a completion and returns a parsed/validated dictionary.
        Supports automatic retries for repair if JSON is truncated.
        """
        use_structured = (
            self.provider != "openrouter"
            and response_schema is not None
        )
        use_json_mode = self.provider != "openrouter"

        raw = await self.complete(
            system=system,
            user=user,
            model=model,
            max_tokens=max_tokens,
            json_mode=use_json_mode,
            response_schema=response_schema if use_structured else None,
        )
        raw = raw.strip()

        # Clean markdown code fences if wrapped by the model
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            if response_schema:
                validated_obj = response_schema.model_validate_json(raw)
                return validated_obj.model_dump()
            return json.loads(raw)
        except (json.JSONDecodeError, ValidationError) as e:
            if max_retries <= 0:
                logger.error(
                    "llm_json_parse_error provider=%s raw=%s error=%s",
                    self.provider,
                    raw[:200],
                    str(e),
                )
                raise ExternalServiceError(
                    "LLM",
                    f"Response was not structurally valid: {str(e)}",
                )

            logger.warning(
                "llm_json_parse_retry provider=%s attempt_left=%d error=%s",
                self.provider,
                max_retries,
                str(e),
            )
            repair_user = (
                "The previous response was cut off and is not valid JSON. "
                "Continue and complete the SAME JSON object exactly where it stopped, "
                "output ONLY the remaining JSON (no commentary, no markdown):\n\n"
                + raw
            )
            return await self.complete_json(
                system=system,
                user=repair_user,
                model=model,
                response_schema=response_schema,
                max_tokens=max_tokens,
                max_retries=max_retries - 1,
            )
