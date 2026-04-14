import time
import logging
import json
import urllib.request
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any, Callable
from openai import OpenAI, APIConnectionError

logger = logging.getLogger("OmniRouter")

class WebSearcher:
    @staticmethod
    def search_tavily(query: str, api_key: str, max_results: int = 5) -> str:
        if not api_key:
            return ""
        try:
            url = "https://api.tavily.com/search"
            data = json.dumps({
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results
            }).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=15.0) as response:
                result = json.loads(response.read().decode('utf-8'))
                
                context = []
                for res in result.get('results', []):
                    context.append(f"[{res.get('title', 'No Title')}] ({res.get('url', '')}):\n{res.get('content', '')}")
                
                if not context:
                    return ""
                    
                context_str = "\n\n".join(context)
                return f"\n\n### WEB SEARCH RESULTS ###\n{context_str}\n### END WEB SEARCH RESULTS ###\n"
        except Exception as e:
            logger.warning(f"⚠️ Tavily web search failed: {e}")
            return ""

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

    def fetch_free_models(self) -> List[str]:
        """Fetch available free models dynamically."""
        if not self._base_url:
            return []
            
        # 1. OpenRouter (API provides explicit pricing)
        if "openrouter.ai" in self._base_url:
            try:
                url = "https://openrouter.ai/api/v1/models"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=10.0) as response:
                    data = json.loads(response.read().decode('utf-8'))
                    free_models = []
                    for model in data.get('data', []):
                        pricing = model.get('pricing', {})
                        if pricing.get('prompt') == "0" and pricing.get('completion') == "0":
                            free_models.append(model.get('id'))
                    return free_models
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch free models from OpenRouter {self.name}: {e}")
                return []
                
        # 2. Providers inherently free (e.g. Google/Gemini, Groq, local inference like Ollama/LM Studio)
        # Here we assume all models accessible via these endpoints are free.
        free_endpoints = ["api.groq.com", "generativelanguage.googleapis.com", "localhost", "127.0.0.1"]
        if any(domain in self._base_url for domain in free_endpoints):
            try:
                client = self._get_client()
                if client:
                    models = client.models.list()
                    return [m.id for m in models.data]
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch models from free provider {self.name}: {e}")
                return []
                
        # For paid providers (OpenAI, Anthropic), return empty list.
        return []

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

    def get_all_free_models(self) -> Dict[str, List[str]]:
        """Return a dictionary mapping provider names to their list of free models."""
        free_models_map = {}
        for provider_name, provider in self.providers.items():
            try:
                models = provider.fetch_free_models()
                if models:
                    free_models_map[provider_name] = models
            except Exception as e:
                logger.warning(f"⚠️ Error fetching free models for {provider_name}: {e}")
        return free_models_map

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
        extra_body: Optional[Dict] = None,
        enable_web_search: bool = False,
        tavily_api_key: Optional[str] = None
    ) -> Any:
        # Inject Web Search if enabled
        enriched_system = system
        if enable_web_search and tavily_api_key:
            logger.info(f"🌐 Fetching web context for query: {user[:50]}...")
            search_context = WebSearcher.search_tavily(user, tavily_api_key)
            if search_context:
                enriched_system += f"\n\nPlease use the following recent web search results to inform your response if they are relevant:\n{search_context}"

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
                    system=enriched_system,
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
