"""Единая точка вызова LLM. OpenAI и NVIDIA работают через один SDK `openai`."""

from typing import Iterator, Literal

from openai import OpenAI

from core import config

Provider = Literal["openai", "nvidia"]
Message = dict[str, str]  # {"role": "user" | "assistant" | "system", "content": "..."}


def get_client(provider: Provider) -> tuple[OpenAI, str]:
    """Вернуть клиент и модель для провайдера."""
    if provider == "openai":
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY не задан в .env")
        return OpenAI(api_key=config.OPENAI_API_KEY), config.OPENAI_MODEL
    if provider == "nvidia":
        if not config.NVIDIA_API_KEY:
            raise RuntimeError("NVIDIA_API_KEY не задан в .env")
        client = OpenAI(base_url=config.NVIDIA_BASE_URL, api_key=config.NVIDIA_API_KEY)
        return client, config.NVIDIA_MODEL
    raise ValueError(f"Неизвестный провайдер: {provider}")


def chat(messages: list[Message], provider: Provider = "openai") -> str:
    """Получить ответ модели целиком."""
    client, model = get_client(provider)
    response = client.chat.completions.create(model=model, messages=messages)
    return response.choices[0].message.content or ""


def chat_stream(messages: list[Message], provider: Provider = "openai") -> Iterator[str]:
    """Получать ответ модели по кусочкам (для печати в реальном времени)."""
    client, model = get_client(provider)
    stream = client.chat.completions.create(model=model, messages=messages, stream=True)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
