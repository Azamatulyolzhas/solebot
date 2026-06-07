"""
Multi-agent shop assistant powered by CrewAI + Groq.

Flow:
  1. Catalog Analyst — picks relevant items from RAG context
  2. Sales Consultant — writes a short Russian reply for the customer

Enable with USE_CREWAI=true in .env
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import CREWAI_MODEL, GROQ_API_KEY, USE_CREWAI

log = logging.getLogger(__name__)

DEFAULT_CONSULTANT_BACKSTORY = (
    "Ты дружелюбный консультант магазина кроссовок. "
    "Отвечаешь кратко, по-русски, только по фактам из каталога."
)


def crewai_enabled(groq_api_key: str | None = None) -> bool:
    key = (groq_api_key or GROQ_API_KEY or "").strip()
    return USE_CREWAI and bool(key)


def _format_history(history: list[dict[str, str]], limit: int = 4) -> str:
    if not history:
        return "Нет предыдущих сообщений."
    lines: list[str] = []
    for msg in history[-limit:]:
        role = "Клиент" if msg.get("role") == "user" else "Бот"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines) or "Нет предыдущих сообщений."


def _build_crew(
    user_message: str,
    product_context: str,
    history: list[dict[str, str]],
    shop_prompt: str | None = None,
    groq_api_key: str | None = None,
):
    from crewai import Agent, Crew, LLM, Process, Task

    llm = LLM(
        model=f"groq/{CREWAI_MODEL}",
        api_key=(groq_api_key or GROQ_API_KEY),
        temperature=0.3,
    )

    consultant_backstory = (shop_prompt or "").strip() or DEFAULT_CONSULTANT_BACKSTORY
    history_text = _format_history(history)

    catalog_analyst = Agent(
        role="Аналитик каталога",
        goal="Найти в каталоге товары, которые подходят под запрос клиента",
        backstory=(
            "Ты эксперт по кроссовкам. Работаешь только с данными склада — "
            "не выдумываешь модели, цены и остатки."
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )

    consultant = Agent(
        role="Консультант магазина",
        goal="Сформулировать короткий полезный ответ клиенту на русском языке",
        backstory=consultant_backstory,
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )

    analyze_task = Task(
        description=(
            f"Запрос клиента: {user_message}\n\n"
            f"История диалога:\n{history_text}\n\n"
            f"Каталог склада (единственный источник правды):\n{product_context}\n\n"
            "Выбери подходящие позиции. Укажи бренд, модель, размер, цену в ₸ и остаток. "
            "Если точного совпадения нет — предложи ближайшие альтернативы из каталога."
        ),
        expected_output=(
            "Структурированный список релевантных товаров с ценами и остатками "
            "или явное сообщение, что в каталоге нет подходящих позиций."
        ),
        agent=catalog_analyst,
    )

    reply_task = Task(
        description=(
            "На основе анализа каталога напиши ответ клиенту.\n"
            "Правила:\n"
            "- 2–3 предложения, русский язык\n"
            "- Цены только в ₸\n"
            "- Не выдумывай товары — только из анализа\n"
            "- Если ничего не найдено — вежливо попроси уточнить бренд/модель/размер"
        ),
        expected_output="Готовый текст ответа для клиента без пояснений и markdown.",
        agent=consultant,
        context=[analyze_task],
    )

    return Crew(
        agents=[catalog_analyst, consultant],
        tasks=[analyze_task, reply_task],
        process=Process.sequential,
        verbose=False,
    )


def _run_crew_sync(
    user_message: str,
    product_context: str,
    history: list[dict[str, str]],
    shop_prompt: str | None = None,
    groq_api_key: str | None = None,
) -> str:
    crew = _build_crew(user_message, product_context, history, shop_prompt, groq_api_key)
    result: Any = crew.kickoff()
    text = str(getattr(result, "raw", None) or result).strip()
    if not text:
        raise ValueError("CrewAI returned empty response")
    return text


async def ask_crew(
    user_message: str,
    product_context: str,
    history: list[dict[str, str]],
    shop_prompt: str | None = None,
    groq_api_key: str | None = None,
) -> str:
    """Run the shop crew asynchronously (non-blocking for FastAPI/Telegram)."""
    return await asyncio.to_thread(
        _run_crew_sync,
        user_message,
        product_context,
        history,
        shop_prompt,
        groq_api_key,
    )
