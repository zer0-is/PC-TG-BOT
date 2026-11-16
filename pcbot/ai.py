"""Запросы к OpenRouter."""


from openai import AsyncOpenAI

from .config import OPENROUTER_API_KEY, OPENROUTER_MODEL


async def ask_openrouter(prompt: str) -> str:
    async with AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        timeout=45,
        max_retries=1,
    ) as client:
        response = await client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
        )
    answer = response.choices[0].message.content
    if not answer or not answer.strip():
        raise RuntimeError("Модель вернула пустой ответ")
    return answer.strip()
