import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any, Callable
from openai import OpenAI, APIConnectionError

logger = logging.getLogger("OmniRouter")

class AIProvider(ABC):
    def __init__(self, name: str, api_key: str, base_url: Optional[str] = None):
        self.name = name
        self._api_key = api_key
        self._base_url = base_url
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> Optional[OpenAI]:
        if not self._client and self._api_key:
            try:
                # Handle pydantic SecretStr gracefully if passed
                key_value = self._api_key.get_secret_value() if hasattr(self._api_key, 'get_secret_value') else self._api_key
                self._client = OpenAI(
                    api_key=key_value,
                    base_url=self._base_url,
                    timeout=30.0,
                    max_retries=1
                )
                logger.info(f"✅ AI Client Connected: {self.name}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to init {self.name}: {e}")
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def get_rate_limiter(self) -> Any:
        return None

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        cost_callback: Optional[Callable] = None,
        sanitize_func: Optional[Callable] = None,
        extra_body: Optional[Dict] = None
    ) -> str:
        if not self.is_available():
            raise RuntimeError(f"Provider {self.name} is not available (Missing Key).")

        limiter = self.get_rate_limiter()
        if limiter and hasattr(limiter, "wait_if_needed"):
            if not limiter.wait_if_needed(timeout=30):
                raise RuntimeError(f"Rate limit timeout for {self.name}")

        clean_user = sanitize_func(user) if sanitize_func else user
        estimated_input_tokens = len(clean_user) // 4
        input_tokens = estimated_input_tokens
        output_tokens = 0
        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                client = self._get_client()
                if not client:
                    raise RuntimeError(f"Failed to create client for {self.name}")
                kwargs = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": (system or "").strip()},
                        {"role": "user", "content": clean_user},
                    ],
                    "temperature": float(temperature),
                    "max_tokens": int(max_tokens),
                }
                if extra_body:
                    kwargs["extra_body"] = extra_body

                resp = client.chat.completions.create(**kwargs)
                content = (resp.choices[0].message.content or "").strip()

                if hasattr(resp, 'usage') and resp.usage:
                    input_tokens = getattr(resp.usage, 'prompt_tokens', estimated_input_tokens)
                    output_tokens = getattr(resp.usage, 'completion_tokens', len(content) // 4)
                else:
                    output_tokens = len(content) // 4

                if cost_callback:
                    cost_callback(self.name, model, True, input_tokens, output_tokens)
                return content

            except APIConnectionError as e:
                last_error = e
                if attempt < max_retries:
                    wait_time = 2 * (attempt + 1)
                    logger.warning(f"⚠️ {self.name} connection failed (Attempt {attempt+1}), retrying in {wait_time}s...")
                    self._client = None
                    time.sleep(wait_time)
                    continue
                else:
                    break

            except Exception as e:
                if cost_callback:
                    cost_callback(self.name, model, False, input_tokens, 0)
                error_str = str(e)
                if "429" in error_str or "quota" in error_str.lower() or "limit" in error_str.lower():
                    if limiter and hasattr(limiter, "reset"):
                        limiter.reset()
                    raise RuntimeError(f"Quota exhausted or limit reached: {self.name} - {error_str}")
                logger.error(f"❌ API call failed for {self.name}/{model}: {error_str[:200]}")
                raise

        if cost_callback:
            cost_callback(self.name, model, False, input_tokens, 0)
        raise RuntimeError(f"Connection failed after retries for {self.name}: {last_error}")

class AIRouter:
    def __init__(self, providers: Dict[str, AIProvider], model_routing: Dict[str, List[Dict[str, str]]]):
        self.providers = providers
        self.model_routing = model_routing

    def get_provider(self, name: str) -> Optional[AIProvider]:
        return self.providers.get(name)

    def chat_complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1000,
        routing_key: str = "default",
        cost_callback: Optional[Callable] = None,
        sanitize_func: Optional[Callable] = None,
        on_provider_success: Optional[Callable] = None,
        return_metadata: bool = False,
        extra_body: Optional[Dict] = None
    ) -> Any:
        candidates = self.model_routing.get(routing_key, self.model_routing.get("default", []))
        last_exception = None
        attempted_providers = []

        for candidate in candidates:
            model_name = candidate["model"]
            provider_name = candidate["provider"]
            provider = self.get_provider(provider_name)
            if not provider or not provider.is_available():
                continue

            logger.info(f"🧠 [AI] Invoking: {model_name} ({provider_name})...")
            attempted_providers.append(f"{provider_name}/{model_name}")

            try:
                result = provider.complete(
                    model=model_name,
                    system=system,
                    user=user,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    cost_callback=cost_callback,
                    sanitize_func=sanitize_func,
                    extra_body=extra_body
                )
                if on_provider_success:
                    on_provider_success(provider_name, model_name)
                logger.info(f"✅ Success: {model_name} ({provider_name})")
                
                if return_metadata:
                    return {
                        "success": True,
                        "content": result,
                        "provider": provider_name,
                        "model": model_name
                    }
                return result

            except RuntimeError as e:
                err_msg = str(e)
                if "Quota exhausted" in err_msg or "Rate limit timeout" in err_msg or "Connection failed" in err_msg or "limit reached" in err_msg:
                    logger.warning(f"⚠️ {model_name} issue, trying next provider... ({err_msg})")
                    last_exception = e
                    continue
                raise

            except Exception as e:
                logger.warning(f"⚠️ Error with {model_name} ({provider_name}): {e}")
                last_exception = e
                time.sleep(1)
                continue

        attempted_str = " → ".join(attempted_providers)
        err_msg = f"❌ All AI models failed after trying: {attempted_str}\nLast error: {last_exception}"
        
        if return_metadata:
            return {
                "success": False,
                "content": err_msg,
                "provider": None,
                "model": None
            }
        raise RuntimeError(err_msg)
