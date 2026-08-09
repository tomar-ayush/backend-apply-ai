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
        Base completion coordinator across all LLM providers with automatic model fallback.
        """
        import asyncio

        fallback_models = {
            "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
            "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
            "gemini": ["gemini-3.6-flash", "gemini-3.1-pro", "gemini-3.5-pro"],
            "openrouter": ["openai/gpt-4o", "openai/gpt-4o-mini"]
        }
        
        provider_key = self.provider or "openai"
        default_models = fallback_models.get(provider_key, [])
        
        models_to_try = [model] if model else (
            [default_models[0]] if default_models else []
        )
        
        # Add fallback models if they aren't already in the try list
        for fb_model in default_models:
            if fb_model not in models_to_try:
                models_to_try.append(fb_model)

        last_error = None
        
        for attempt_model in models_to_try:
            try:
                if self.provider == "openai":
                    return await self._openai_complete(
                        system,
                        user,
                        attempt_model,
                        max_tokens,
                        json_mode,
                        response_schema,
                    )
                if self.provider == "openrouter":
                    return await self._openai_complete(
                        system,
                        user,
                        attempt_model or "openrouter/free",
                        max_tokens,
                        json_mode,
                        response_schema,
                    )
                if self.provider == "anthropic":
                    return await self._anthropic_complete(
                        system, user, attempt_model, max_tokens, response_schema
                    )
                if self.provider == "gemini":
                    return await self._gemini_complete(
                        system,
                        user,
                        attempt_model,
                        max_tokens,
                        json_mode,
                        response_schema,
                    )
            except (ExternalServiceError, BadRequestError) as e:
                # If it's a structural/auth error, fallback might not help, but for 
                # generic API errors (503, 429), falling back is good.
                logger.warning(
                    "llm_model_fallback provider=%s failed_model=%s error=%s", 
                    self.provider, attempt_model, str(e)
                )
                last_error = e
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(
                    "llm_model_fallback provider=%s failed_model=%s error=%s", 
                    self.provider, attempt_model, str(e)
                )
                last_error = e
                await asyncio.sleep(1)

        logger.error(
            "llm_error_all_models_failed provider=%s error=%s", self.provider, str(last_error)
        )
        raise ExternalServiceError("LLM", str(last_error))

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
            try:
                res = await self._client.beta.chat.completions.parse(
                    response_format=response_schema, **kwargs
                )
                return res.choices[0].message.content or ""
            except Exception as e:
                logger.info(
                    "openai_parse_structured_output_fallback provider=%s model=%s error=%s",
                    self.provider,
                    model_name,
                    str(e),
                )

        if json_mode or response_schema:
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
            try:
                response = await self._client.beta.messages.parse(
                    response_format=response_schema, **kwargs
                )
                return response.text
            except Exception as e:
                logger.info(
                    "anthropic_parse_structured_output_fallback model=%s error=%s",
                    model_name,
                    str(e),
                )

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
        Supports automatic retries for repair if JSON is invalid or missing required fields.
        """
        system_prompt = system
        if response_schema:
            schema_json = json.dumps(
                response_schema.model_json_schema(), indent=2
            )
            if "JSON Schema" not in system_prompt:
                system_prompt = (
                    f"{system}\n\n"
                    f"IMPORTANT: You MUST respond ONLY with a single valid JSON object adhering strictly to this JSON Schema:\n"
                    f"```json\n{schema_json}\n```\n"
                    f"Do NOT include markdown formatting, explanations, or conversational text."
                )

        raw = await self.complete(
            system=system_prompt,
            user=user,
            model=model,
            max_tokens=max_tokens,
            json_mode=True,
            response_schema=response_schema,
        )
        raw = (raw or "").strip()

        # Clean markdown code fences (e.g. ```json ... ``` or ``` ...)
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        # Clean surrounding quotation marks if present
        quote_chars = "\"'`“”"
        while (
            raw
            and len(raw) >= 2
            and raw[0] in quote_chars
            and raw[-1] in quote_chars
        ):
            raw = raw[1:-1].strip()

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
            repair_system = (
                f"{system_prompt}\n\n"
                f"CRITICAL: Your previous response failed schema validation or was invalid JSON. "
                f"Error details: {str(e)}\n"
                f"You MUST regenerate and return the COMPLETE, single valid JSON object matching all required fields."
            )
            repair_user = (
                f"{user}\n\n"
                f"--- PREVIOUS FAILURE DETAILS ---\n"
                f"Validation Error: {str(e)}\n"
                f"Previous Response Preview: {raw[:300]}\n"
                f"--- END PREVIOUS FAILURE DETAILS ---\n\n"
                f"Please regenerate and output the COMPLETE, valid JSON object from start to finish."
            )
            return await self.complete_json(
                system=repair_system,
                user=repair_user,
                model=model,
                response_schema=response_schema,
                max_tokens=max_tokens,
                max_retries=max_retries - 1,
            )
