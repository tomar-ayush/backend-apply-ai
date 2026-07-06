import json
from app.common.logging import get_logger
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel, ValidationError

from app.common.exceptions import ExternalServiceError, BadRequestError

logger = get_logger(__name__)

PROVIDER_ALIASES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
}


class LLMClient:
    def __init__(self, provider: str, api_key: str):
        self.provider = PROVIDER_ALIASES.get(provider.lower())
        if self.provider is None:
            raise BadRequestError(
                f"Unsupported LLM provider: '{provider}'. "
                f"Supported: openai, anthropic, gemini"
            )
        self.api_key = api_key
        self._client = self._build_client()

    def _build_client(self):
        if self.provider == "openai":
            try:
                from openai import AsyncOpenAI
                return AsyncOpenAI(api_key=self.api_key)
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
                raise BadRequestError("google-genai package not installed")

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
        Base completion coordinator. 
        Accepts response_schema parameter to enable direct structured model restrictions.
        """
        try:
            if self.provider == "openai":
                return await self._openai_complete(system, user, model, max_tokens, json_mode, response_schema)
            if self.provider == "anthropic":
                return await self._anthropic_complete(system, user, model, max_tokens, response_schema)
            if self.provider == "gemini":
                return await self._gemini_complete(system, user, model, max_tokens, json_mode, response_schema)
        except (ExternalServiceError, BadRequestError):
            raise
        except Exception as e:
            logger.error("llm_error provider=%s error=%s", self.provider, str(e))
            raise ExternalServiceError("LLM", str(e))

    async def _openai_complete(
        self, system: str, user: str, model: Optional[str], max_tokens: int, json_mode: bool, response_schema: Optional[Type[BaseModel]]
    ) -> str:
        model = model or "gpt-4o"
        kwargs: dict = dict(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
        )
        
        if response_schema:
            # Native OpenAI Structured Outputs parsing pipeline
            res = await self._client.beta.chat.completions.parse(
                response_format=response_schema,
                **kwargs
            )
            return res.choices[0].message.content
        
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
            
        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    async def _anthropic_complete(
        self, system: str, user: str, model: Optional[str], max_tokens: int, response_schema: Optional[Type[BaseModel]]
    ) -> str:
        model = model or "claude-3-5-sonnet-latest"
        kwargs: dict = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        
        if response_schema:
            # Native Anthropic Structured Outputs tracking pipeline
            response = await self._client.beta.messages.parse(
                response_format=response_schema,
                **kwargs
            )
            return response.text
            
        response = await self._client.messages.create(**kwargs)
        return response.content[0].text

    async def _gemini_complete(
        self, system: str, user: str, model: Optional[str], max_tokens: int, json_mode: bool, response_schema: Optional[Type[BaseModel]]
    ) -> str:
        from google.genai import types

        model_name = model or "gemini-3.5-flash"
        config_args = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
        }
        
        if response_schema:
            config_args["response_mime_type"] = "application/json"
            config_args["response_schema"] = response_schema
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
        return response.text

    async def complete_json(
        self,
        system: str,
        user: str,
        model: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """
        Executes a completion run and enforces JSON deserialization validation.
        """
        raw = await self.complete(
            system=system, 
            user=user, 
            model=model, 
            max_tokens=max_tokens, 
            json_mode=True, 
            response_schema=response_schema
        )
        raw = raw.strip()
        
        # Clean up fallback code block wrappers safely if returned by a non-schema tier fallback
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]        
        try:
            if response_schema:
                # Validates raw string structurally straight against the Pydantic data layout
                validated_obj = response_schema.model_validate_json(raw)
                return validated_obj.model_dump()
            return json.loads(raw)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error("llm_json_parse_error provider=%s raw=%s error=%s", self.provider, raw[:200], str(e))
            raise ExternalServiceError("LLM", f"Response was not structurally valid: {str(e)}")