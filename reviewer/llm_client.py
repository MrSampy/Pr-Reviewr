import os

import httpx
import openai
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_URL = "http://localhost:11434"
_DEFAULT_MODEL = "qwen2.5-coder:1.5b" # "qwen2.5-coder:7b"
_TIMEOUT = 120
_AVAILABILITY_TIMEOUT = 5


def _get_config() -> tuple[str, str]:
    url = os.getenv("OLLAMA_URL", _DEFAULT_URL).rstrip("/")
    model = os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)
    return url, model


def review(prompt: str) -> str:
    """Send prompt to Ollama and return the raw model response string."""
    url, model = _get_config()
    client = openai.OpenAI(
        base_url=f"{url}/v1",
        api_key="ollama",
        timeout=_TIMEOUT,
    )
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2000,
        )
    except openai.APITimeoutError:
        raise TimeoutError(f"LLM timeout after {_TIMEOUT}s")
    except openai.APIConnectionError:
        raise RuntimeError(f"Ollama is not running at {url}")

    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise RuntimeError("LLM returned empty response")

    return content.strip()


def is_available() -> bool:
    """Return True if Ollama is running and the configured model is loaded."""
    url, model = _get_config()
    try:
        response = httpx.get(f"{url}/api/tags", timeout=_AVAILABILITY_TIMEOUT)
        response.raise_for_status()
        tags = response.json()
        model_names = [m.get("name", "") for m in tags.get("models", [])]
        return any(name == model or name.startswith(model.split(":")[0]) for name in model_names)
    except Exception:
        return False


if __name__ == "__main__":
    url, model = _get_config()
    print(f"Checking Ollama at {url} (model: {model}) ...")

    if not is_available():
        print("Ollama is not available or model is not loaded.")
    else:
        print("Ollama is available. Sending test prompt ...\n")
        result = review("Ответь только валидным JSON: {'comments': []}")
        print(result)
