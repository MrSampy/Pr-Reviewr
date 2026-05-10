import os
from typing import Any, Dict, Iterable, List

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_BATCH_SIZE = 32


def get_ollama_config() -> Dict[str, str]:
    api_url = os.getenv("OLLAMA_API_URL", os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    model = os.getenv("OLLAMA_EMBED_MODEL", DEFAULT_MODEL)
    return {"api_url": api_url.rstrip("/"), "model": model}


def _batch(iterable: List[Any], batch_size: int) -> Iterable[List[Any]]:
    for i in range(0, len(iterable), batch_size):
        yield iterable[i : i + batch_size]


def embed_chunks(
    chunks: List[Dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: float = 30.0,
) -> List[List[float]]:
    """Embed a list of chunks via Ollama's Nomic embedding model in batches.

    Args:
        chunks: List of chunk dictionaries containing a `content` key.
        batch_size: Number of chunks sent per request.
        timeout: HTTP request timeout in seconds.

    Returns:
        List of embedding vectors aligned with the input chunks.
    """
    if not chunks:
        return []

    config = get_ollama_config()
    api_url = config["api_url"]
    model = config["model"]
    endpoint = f"{api_url}/v1/embeddings"

    texts = []
    for chunk in chunks:
        content = chunk.get("content")
        if content is None:
            raise ValueError("Each chunk must include a `content` field.")
        texts.append(str(content))

    embeddings: List[List[float]] = []
    headers = {"Content-Type": "application/json"}

    with httpx.Client(timeout=timeout, headers=headers) as client:
        for batch in _batch(texts, batch_size):
            payload = {"model": model, "input": batch}
            response = client.post(endpoint, json=payload)
            response.raise_for_status()
            body = response.json()
            if "data" not in body:
                raise RuntimeError(f"Unexpected Ollama response format: {body}")

            batch_embeddings = [item.get("embedding") for item in body["data"]]
            if any(embed is None for embed in batch_embeddings):
                raise RuntimeError("Missing embedding vector in Ollama response data.")

            embeddings.extend(batch_embeddings)

    if len(embeddings) != len(chunks):
        raise RuntimeError("Embedding count does not match input chunk count.")

    return embeddings


def embed_texts(
    texts: List[str], batch_size: int = DEFAULT_BATCH_SIZE, timeout: float = 30.0
) -> List[List[float]]:
    """Embed a list of text strings via Ollama batch API."""
    chunks = [{"content": text} for text in texts]
    return embed_chunks(chunks, batch_size=batch_size, timeout=timeout)


if __name__ == "__main__":
    sample = [
        {"content": "function foo() { return 1; }"},
        {"content": 'public void DoWork() { Console.WriteLine("Hello"); }'},
    ]
    vectors = embed_chunks(sample, batch_size=2)
    print(f"Embedded {len(vectors)} items")
