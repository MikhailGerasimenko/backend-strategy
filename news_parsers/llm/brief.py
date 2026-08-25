from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..database import NewsRow, fetch_news_for_period
from ..periods import PeriodRange
from .config import (
    BRIEF_CHUNK_PAUSE_SEC,
    BRIEF_CHUNK_SIZE,
    BRIEF_CHUNK_THRESHOLD,
    BRIEF_MODEL,
    MAX_NEWS_IN_PROMPT,
    MAX_TOKENS,
    NEWS_BODY_CHARS,
)
from .openrouter import chat_completion
from .kallanish_docx import build_kallanish_block
from .reference_data import (
    default_format_pdf_path,
    default_indicators_path,
    load_format_excerpt,
)
from .system_prompts import CORPORATE_SYSTEM_PROMPT, MARKET_SYSTEM_PROMPT

PROMPT_VERSION = "daily_digest_v18_clean_batch_glue"

SYSTEM_PROMPT = """Ты — старший аналитик корпоративной стратегии «Северстали».
Сформируй качественный ежедневный бриф для руководства компании.
Язык: русский, деловой, конкретный. Пиши как аналитик, а не как новостной агрегатор.

КРИТИЧЕСКИЕ ТРЕБОВАНИЯ К КАЧЕСТВУ:
1) Никакой «воды»: только факты, цифры, причинно-следственные связи и значение события для рынка/компаний.
2) Нельзя заполнять разделы списком источников, URL, названий СМИ или перечислением ссылок вместо содержания.
3) Не выдумывай факты, компании, цифры, ссылки, авторов и источники.
4) Если данных по подпункту нет, пиши «Релевантных новостей не выявлено.»
5) Исключай нерелевантные темы: нержавеющая сталь, мелкие региональные события, локальные юридические споры и новости без влияния на рынок стали/сырья/ключевые сектора спроса.
6) Для рыночных индикаторов не перечисляй все доступные индексы. Выбирай 1-2 ключевых показателя, которые лучше всего объясняют динамику.

ПРАВИЛО ПО ИСТОЧНИКАМ:
- Нигде в брифе НЕ добавляй строки «Источник:», «Источники:», URL и названия изданий.
- Не ссылайся на источники в скобках. Пиши только аналитическое содержание.

ПРАВИЛО «АНАЛИТИЧЕСКИЙ КОММЕНТАРИЙ»:
- Не добавляй строку «Аналитический комментарий» в разделах 1-6 и в резюме.
- В разделах 8-10 НЕ добавляй отдельную строку «Аналитический комментарий», если в материалах нет явного комментария аналитика/менеджмента/эксперта.
- В разделе «Результаты компаний» при наличии включай комментарий аналитиков/менеджмента из материалов (автор/организация, цитата или смысловая выдержка) внутри текста новости, без отдельной служебной строки.
- Не пиши ожидаемые эффекты ради заполнения. Если данных нет — не выдумывай.

ЕДИНЫЙ ФОРМАТ РАЗДЕЛОВ 1-5 (подзаголовок + буллиты):
- Название подпункта — отдельной строкой; для продуктовых линий **Железная руда…**, **Коксующийся уголь**, **Стальной лом…**, **ГК прокат** используй жирное выделение `**...**`.
- Под подзаголовком — буллиты `-` с фактами (цены, объёмы, драйверы). Каждая новость — 1-3 предложения в одном или нескольких буллитах.
- Без нумерации подпунктов, без полей «Ключ: значение».

ЕДИНЫЙ ФОРМАТ НОВОСТНЫХ РАЗДЕЛОВ 6-10 (как в шаблоне брифа):
- Каждая новость — отдельный нумерованный блок: `1.`, `2.`, `3.` …
- Первая строка блока: номер + **краткий заголовок-саммари** + суть в 1-2 предложениях (что произошло и почему важно).
- Далее при необходимости 2-4 буллита `-` с деталями (цифры, продукт, география, сроки, метрики).
- Между блоками новостей оставляй пустую строку.
- Не используй формат «Поле: значение» списком подряд — только связный текст и буллиты.

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА:
## Ежедневный рыночный дайджест ({текущая дата})

## РЕЗЮМЕ ({дата})
6-10 буллитов: главные выводы дня для CEO. Каждый буллит содержательный: что произошло, почему важно, риск/возможность для рынка или «Северстали».

## 1. МИРОВОЙ РЫНОК СТАЛИ И СЫРЬЯ
Одна вводная строка: общая ситуация на **сырьевых и продуктовых рынках** (цены, котировки, спреды, предложение/спрос).
Не делай уклон в отраслевые новости металлургии (производство, корпоративные события, проекты, M&A) — они идут в другие разделы. Здесь только рынок цен на сырьё и прокат.

Подпункты отдельными строками:
**Железная руда в Китае**
**Коксующийся уголь**
**Стальной лом в Турции**
**ГК прокат**

По каждому подпункту: 1-4 буллита — ключевые ценовые индикаторы (1 предложение) и факторы влияния на цены (2-3 предложения).
Для **ГК прокат** — Турция, Китай, ЮВА, экспорт из России (Черное море), если есть данные.
Не включай нержавеющую сталь.

## 2. РЫНОК СТАЛИ И СЫРЬЯ В РОССИИ
Тот же формат, что в разделе 1: цены, котировки, спрос/предложение на российском рынке. Без корпоративных и отраслевых новостей.

Подпункты:
**Железная руда и окатыши в России**
**Коксующийся уголь в России**
**Стальной лом в России**
**ГК прокат в России**

По каждому подпункту: 1-4 буллита с ценами и драйверами. Если нет данных — «Релевантных новостей не выявлено.»

## 3. ЭКОНОМИКИ МИРА
Только **макроэкономика**: ВВП, промпроизводство, PMI, инфляция, ставки, инвестиции, строительство, промышленный цикл.
Не включай новости рынка стали, цен на руду/уголь/прокат и отраслевые металлургические события — они в разделах 1-2.

Подпункты:
Европейский Союз
США
Индия
Китай
Турция

По каждому: 1-3 буллита с макрофактами. Если нет — «Релевантных новостей не выявлено.»

## 4. ЭКОНОМИКА РОССИИ
Подпункты:
Динамика ВВП России
Динамика промышленного производства
Жилищное строительство в России, ипотечное кредитование
Динамика машиностроения в России: автомобилестроение, вагоностроение
Динамика нефтегазового сектора в России: добыча нефти и газа
Оценки настроений в промышленности, PMI
Динамика инфляции в России
Обменный курс рубля
Ключевая ставка в России
Федеральный бюджет
Экономическая политика

По каждому подпункту: 1-3 буллита.
Подпункт «Динамика нефтегазового сектора…» — **только нефть и газ**: добыча, НПЗ, трубопроводы, экспорт/импорт нефти и газа, инвестиции в нефтегаз. **Не включай угольный сектор, угольные шахты, коксующийся/энергетический уголь** — уголь относится к разделам 1-2.
Для обменного курса — прогнозы аналитиков, если есть.
Для федерального бюджета — исполнение бюджета и крупные решения; без мелкой региональной повестки.
Для экономической политики — ДКП, ключевая ставка, налоги, поддержка металлургии/строительства/машиностроения/ипотеки, валютное регулирование.

## 5. БАЛАНСЫ РЫНКА СТАЛИ КИТАЯ
Подпункты:
Динамика производства стали в Китае
Динамика экспорта стали из Китая
Динамика потребления стали в Китае

По каждому: 1-3 буллита. Если нет данных — «Релевантных новостей не выявлено.»

## 6. ГОСУДАРСТВЕННОЕ РЕГУЛИРОВАНИЕ, ТОРГОВЫЕ БАРЬЕРЫ В СТАЛИ И СЫРЬЕ
Формат новостных блоков 6-10 (нумерация, саммари, буллиты, пустая строка между блоками).
По каждой новости: заголовок кейса; суть меры (пошлина, квота, CBAM, санкции, налог, антидемпинг, экспортное/импортное ограничение, субсидия); продукт/рынок; страна/регион; ставка, срок, охват — если есть в материалах.
Не добавляй «Ожидаемый эффект».
Если нет новостей — «Релевантных новостей не выявлено.»

## 7. КАДРОВЫЕ НАЗНАЧЕНИЯ В СТАЛЕЛИТЕЙНЫХ И СЫРЬЕВЫХ КОМПАНИЯХ
Формат новостных блоков 6-10. Только значимые назначения/уходы топ-менеджмента.
Если нет — «Релевантных новостей не выявлено.»

## 8. ПРОЕКТЫ ПО СТРОИТЕЛЬСТВУ И МОДЕРНИЗАЦИИ МОЩНОСТЕЙ
Включай ТОЛЬКО проекты по мощностям:
- сталь и прокат: жидкая сталь, горячий прокат, холодный прокат, оцинкованный прокат, полимерный прокат, динамный прокат;
- чугун;
- сырьё: железная руда, уголь, ГБЖ или ПВЖ;
- экология на металлургических и сырьевых активах.
Исключай логистику, IT, офисы и прочее без прямой связи с перечисленными мощностями.

Формат новостных блоков 6-10. В буллитах при необходимости укажи: тип мощности, компания, локация, мощность, CAPEX, сроки, статус (план / разработка / строительство / модернизация / приостановка / завершение).
Если нет — «Релевантных новостей не выявлено.»

## 9. РЕЗУЛЬТАТЫ КОМПАНИЙ
Включай ТОЛЬКО новости о компаниях из списка (синонимы и аббревиатуры учитывай):
- ПАО «Северсталь»
- Магнитогорский металлургический комбинат (ММК)
- Новолипецкий металлургический комбинат (НЛМК)
- Трубная металлургическая компания (ТМК)
- Уральская Сталь
- Ашинский металлургический завод (АМЗ)
- Металлоинвест
- Мечел
- ArcelorMittal

Другие компании не включай. Формат новостных блоков 6-10.
В буллитах: период, метрики (выручка, EBITDA, прибыль, долг, объёмы, CAPEX — если есть), оценка результата, прогноз, комментарий аналитика/менеджмента из материалов.
Если по списку нет новостей — «Релевантных новостей не выявлено.»

## 10. M&A СДЕЛКИ
Формат новостных блоков 6-10.
В буллитах: кто покупает/продаёт, объект, локация/мощность, стоимость, прочее важное.
Если нет — «Релевантных новостей не выявлено.»

ПРАВИЛА ФОРМАТА:
- Заголовки разделов — `##`.
- Подпункты разделов 1-5 — отдельной строкой; продуктовые линии с `**жирным**`.
- Буллиты `-` — для фактов под подпунктом и внутри нумерованных блоков 6-10.
- Без JSON, таблиц, служебных комментариев, строк источников и URL.
- Не дублируй одну новость дословно в нескольких разделах.
- На выходе — готовый текст брифа для экспорта в Word."""

SYSTEM_PROMPT_VARIANTS: dict[str, dict[str, str]] = {
    "full": {"label": "Полный бриф", "prompt": SYSTEM_PROMPT},
    "market": {"label": "Рыночный бриф", "prompt": MARKET_SYSTEM_PROMPT},
    "corporate": {"label": "Новостной бриф", "prompt": CORPORATE_SYSTEM_PROMPT},
}


def get_system_prompt_variant(variant: str = "full") -> str:
    key = variant if variant in SYSTEM_PROMPT_VARIANTS else "full"
    return SYSTEM_PROMPT_VARIANTS[key]["prompt"]


def list_system_prompt_variants() -> list[dict[str, str]]:
    return [
        {"id": key, "label": item["label"]}
        for key, item in SYSTEM_PROMPT_VARIANTS.items()
    ]


def _resolve_prompt_version(system_prompt: str) -> str:
    normalized = (system_prompt or "").strip()
    for key, item in SYSTEM_PROMPT_VARIANTS.items():
        if normalized == item["prompt"].strip():
            return PROMPT_VERSION if key == "full" else f"{PROMPT_VERSION}_{key}"
    return f"{PROMPT_VERSION}_custom"


USER_PROMPT_TEMPLATE = """Период анализа: {period_label}
Дата комментария: {report_date}

Образец стиля и структуры: {format_excerpt}
---
Материалы Kallanish (учитывай наравне с новостями):
{kallanish_block}
---
Новости за период ({news_count} шт.):
{news_block}

Собери отчёт строго по системному промпту.
В текст брифа не включай строки «Источник», URL и названия СМИ.
{user_tail}"""

USER_PROMPT_TAIL_BY_KIND: dict[str, str] = {
    "full": (
        "Заполни все разделы 1–10 и блок РЕЗЮМЕ. "
        "Не оставляй разделы пустыми: факты или «Релевантных новостей нет»."
    ),
    "market": (
        "Заполни все разделы 1–6 и РЕЗЮМЕ (один абзац). "
        "Под каждым подпунктом — буллиты с фактами или «Релевантных новостей нет»."
    ),
    "corporate": (
        "Заполни все разделы 1–5 и РЕЗЮМЕ (6–10 буллитов). "
        "В разделах — нумерованные блоки новостей или «Релевантных новостей не выявлено.»"
    ),
}

# --- Пошаговая генерация (батчи по ~60 новостей): часть → склейка ---

CHUNK_HINT_BY_KIND: dict[str, str] = {
    "full": (
        "Заполни только те разделы 1–10, для которых в этой части есть материалы; "
        "остальные разделы не выдумывай и не оставляй пустыми заголовками — просто пропусти."
    ),
    "market": (
        "Заполни затронутые подпункты разделов 1–6: буллиты с фактами "
        "или «Релевантных новостей нет»."
    ),
    "corporate": (
        "Заполни затронутые разделы 1–5: нумерованные блоки новостей "
        "или «Релевантных новостей не выявлено.»"
    ),
}

CHUNK_USER_TEMPLATE = """Период: {period_label}
Дата комментария: {report_date}
Черновик части {chunk_index}/{chunk_total}
(в части {chunk_news_count} новостей · всего за день {news_count})

{kallanish_section}Новости этой части:
{news_block}

Задача: подготовь ЧЕРНОВИК фрагмента отчёта по системному промпту.
{chunk_hint}

Правила этой части:
- структура и тон — строго по системному промпту;
- пиши только по материалам выше, без выдуманных фактов;
- блок РЕЗЮМЕ не пиши (его соберёт финальная склейка);
- не выводи одни заголовки без содержания."""

MERGE_HINT_BY_KIND: dict[str, str] = {
    "full": (
        "Итог — полный дайджест (разделы 1–10). "
        "РЕЗЮМЕ — 6–10 содержательных буллитов сразу после заголовка отчёта."
    ),
    "market": (
        "Итог — рыночный дайджест (разделы 1–6). "
        "РЕЗЮМЕ — один связный абзац (минимум 4 предложения), без буллитов."
    ),
    "corporate": (
        "Итог — новостной дайджест (разделы 1–5). "
        "РЕЗЮМЕ — 6–10 содержательных буллитов сразу после заголовка отчёта."
    ),
}

MERGE_USER_TEMPLATE = """Период: {period_label}
Дата комментария: {report_date}
Новостей за день: {news_count}

Материалы Kallanish (при необходимости вплети факты в нужные разделы, без дословного копирования всего текста):
{kallanish_block}

---
Черновики частей (уже написаны по системному промпту):
{fragments}
---

Задача: собери ОДИН итоговый отчёт.
{merge_hint}

Правила склейки:
- итоговая структура, тон и формат — строго по системному промпту;
- объедини одноимённые разделы из всех частей в один связный текст;
- убери дубли одной и той же новости/события;
- не выдумывай факты, которых нет в черновиках и Kallanish;
- каждый раздел/подпункт должен содержать текст (факты или «Релевантных новостей нет» / «не выявлено»);
- не включай в бриф строки «Источник», URL и названия СМИ."""


@dataclass(frozen=True)
class BriefInput:
    period_range: PeriodRange
    news: list[NewsRow | dict[str, Any]]


@dataclass(frozen=True)
class BriefContext:
    indicators_path: Path
    format_pdf_path: Path
    kallanish_docx_path: Path | None = None
    news_dir: Path | None = None
    include_kallanish: bool = True


def default_brief_context(project_dir: Path) -> BriefContext:
    return BriefContext(
        indicators_path=default_indicators_path(project_dir),
        format_pdf_path=default_format_pdf_path(project_dir),
        news_dir=project_dir / "Новости",
    )


def _kallanish_prompt_parts(context: BriefContext) -> tuple[str, Path | None]:
    return build_kallanish_block(
        explicit_path=context.kallanish_docx_path,
        news_dir=context.news_dir,
        include=context.include_kallanish,
    )


def generate_brief_comment(
    brief_input: BriefInput,
    *,
    model: str | None = None,
    context: BriefContext | None = None,
    project_dir: Path | None = None,
    single_pass: bool = False,
    system_prompt: str | None = None,
    brief_kind: str = "full",
) -> tuple[str, dict[str, Any]]:
    effective_system = (system_prompt or "").strip() or SYSTEM_PROMPT
    if context is None:
        context = default_brief_context(project_dir or Path.cwd())

    news_total = len(brief_input.news)
    use_chunks = not single_pass and news_total > BRIEF_CHUNK_THRESHOLD

    if use_chunks:
        content, extra = _generate_brief_chunked(
            brief_input,
            context,
            model=model or BRIEF_MODEL,
            system_prompt=effective_system,
            brief_kind=brief_kind,
        )
        generation_mode = "chunked"
        news_in_prompt = news_total
        api_calls = extra["api_calls"]
    else:
        user_prompt, news_in_prompt = build_user_prompt(
            brief_input, context, brief_kind=brief_kind,
        )
        content, api_calls = _complete_brief_with_retry(
            effective_system,
            user_prompt,
            model=model or BRIEF_MODEL,
            brief_kind=brief_kind,
            max_tokens=MAX_TOKENS,
            temperature=0.25,
        )
        generation_mode = "single"

    prompt_version = _resolve_prompt_version(effective_system)

    sources = set()
    blocks: set[str] = set()
    for item in brief_input.news:
        if hasattr(item, "source"):
            sources.add(item.source)
            blocks.add(getattr(item, "keyword_block", "") or "")
        else:
            sources.add(item.get("source", ""))
            blocks.add(item.get("keyword_block", "") or "")
    metadata = {
        "prompt_version": prompt_version,
        "brief_kind": brief_kind,
        "generation_mode": generation_mode,
        "content_chars": len(content.strip()),
        "custom_system_prompt": bool(system_prompt and system_prompt.strip()),
        "api_calls": api_calls,
        "news_count": news_total,
        "news_in_prompt": news_in_prompt,
        "news_omitted_from_prompt": max(0, news_total - news_in_prompt),
        "sources_count": len(sources),
        "keyword_blocks": sorted(block for block in blocks if block),
        "period": brief_input.period_range.name,
        "model": model or BRIEF_MODEL,
        "indicators_file": str(context.indicators_path),
        "format_reference": str(context.format_pdf_path),
        **_kallanish_metadata(context),
    }
    return content, metadata


def _kallanish_metadata(context: BriefContext) -> dict[str, Any]:
    block, path = _kallanish_prompt_parts(context)
    return {
        "kallanish_file": str(path) if path else "",
        "kallanish_in_prompt": bool(path),
        "kallanish_chars": len(block) if path else 0,
    }


def _split_news_chunks(
    news: Sequence[NewsRow | dict[str, Any]],
    chunk_size: int,
) -> list[list[NewsRow | dict[str, Any]]]:
    items = list(news)
    if not items:
        return []
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _generate_brief_chunked(
    brief_input: BriefInput,
    context: BriefContext,
    *,
    model: str,
    system_prompt: str,
    brief_kind: str = "full",
) -> tuple[str, dict[str, Any]]:
    period = brief_input.period_range
    period_label = f"{period.start.isoformat()} — {period.end.isoformat()} ({period.name})"
    report_date = period.end.strftime("%d.%m.%Y")
    news_total = len(brief_input.news)
    chunks = _split_news_chunks(brief_input.news, BRIEF_CHUNK_SIZE)
    chunk_total = len(chunks)

    print(
        f"Chunked generation: {news_total} news → {chunk_total} parts "
        f"(~{BRIEF_CHUNK_SIZE} news each, pause {BRIEF_CHUNK_PAUSE_SEC}s)",
        flush=True,
    )

    kallanish_block, kallanish_path = _kallanish_prompt_parts(context)
    kallanish_loaded = kallanish_path is not None
    fragments: list[str] = []
    for index, chunk_news in enumerate(chunks, start=1):
        if index > 1:
            time.sleep(BRIEF_CHUNK_PAUSE_SEC)
        news_block, _ = format_news_block_grouped(
            chunk_news,
            max_items=len(chunk_news),
        )
        if kallanish_loaded and index == 1:
            kallanish_section = (
                "Материалы Kallanish (учитывай наравне с новостями):\n"
                f"{kallanish_block}\n\n"
            )
        else:
            kallanish_section = ""
        chunk_prompt = CHUNK_USER_TEMPLATE.format(
            period_label=period_label,
            report_date=report_date,
            chunk_index=index,
            chunk_total=chunk_total,
            chunk_news_count=len(chunk_news),
            news_count=news_total,
            kallanish_section=kallanish_section,
            news_block=news_block,
            chunk_hint=CHUNK_HINT_BY_KIND.get(brief_kind, CHUNK_HINT_BY_KIND["full"]),
        )
        print(f"  Part {index}/{chunk_total} ({len(chunk_news)} news)...", flush=True)
        part = chat_completion(
            system_prompt=system_prompt,
            user_prompt=chunk_prompt,
            model=model,
            max_tokens=min(MAX_TOKENS, 16000),
            temperature=0.25,
        )
        fragments.append(f"### Черновик части {index}/{chunk_total}\n\n{part}")

    time.sleep(BRIEF_CHUNK_PAUSE_SEC)
    merge_prompt = MERGE_USER_TEMPLATE.format(
        period_label=period_label,
        report_date=report_date,
        news_count=news_total,
        kallanish_block=kallanish_block,
        fragments="\n\n".join(fragments),
        merge_hint=MERGE_HINT_BY_KIND.get(brief_kind, MERGE_HINT_BY_KIND["full"]),
    )
    print("  Merging parts into final digest...", flush=True)
    merged, merge_calls = _complete_brief_with_retry(
        system_prompt,
        merge_prompt,
        model=model,
        brief_kind=brief_kind,
        max_tokens=MAX_TOKENS,
        temperature=0.2,
    )
    return merged, {"api_calls": chunk_total + merge_calls}


def _is_brief_too_short(content: str, brief_kind: str) -> bool:
    stripped = (content or "").strip()
    if len(stripped) < 600:
        return True
    headings = len(re.findall(r"^##\s+", stripped, re.MULTILINE))
    bullets = stripped.count("\n- ")
    numbered = len(re.findall(r"^\d+\.\s+", stripped, re.MULTILINE))
    min_sections = {"market": 4, "corporate": 3, "full": 6}.get(brief_kind, 4)
    if headings >= 2 and bullets + numbered < 3:
        return True
    if headings < min_sections and bullets + numbered < 5:
        return True
    return False


_BRIEF_RETRY_SUFFIX = (
    "\n\nКРИТИЧНО: предыдущая попытка дала неполный ответ (только заголовки или пустые разделы). "
    "Сформируй ПОЛНЫЙ бриф: каждый раздел системного промпта должен содержать содержательный текст, "
    "буллиты или фразу «Релевантных новостей не выявлено.» / «Релевантных новостей нет». "
    "Блок РЕЗЮМЕ заполни полностью."
)


def _complete_brief_with_retry(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    brief_kind: str,
    max_tokens: int,
    temperature: float,
) -> tuple[str, int]:
    content = chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if not _is_brief_too_short(content, brief_kind):
        return content, 1
    print(
        f"  Brief too short ({len(content.strip())} chars), retrying…",
        flush=True,
    )
    retry = chat_completion(
        system_prompt=system_prompt,
        user_prompt=user_prompt + _BRIEF_RETRY_SUFFIX,
        model=model,
        max_tokens=max_tokens,
        temperature=min(temperature + 0.05, 0.35),
    )
    if len(retry.strip()) > len(content.strip()):
        return retry, 2
    return content, 2


def build_user_prompt(
    brief_input: BriefInput,
    context: BriefContext,
    *,
    brief_kind: str = "full",
) -> tuple[str, int]:
    period = brief_input.period_range
    period_label = f"{period.start.isoformat()} — {period.end.isoformat()} ({period.name})"
    report_date = period.end.strftime("%d.%m.%Y")
    format_excerpt = ""
    if context.format_pdf_path.exists():
        format_excerpt = load_format_excerpt(context.format_pdf_path)
    kallanish_block, _ = _kallanish_prompt_parts(context)
    news_block, news_in_prompt = format_news_block_grouped(brief_input.news)
    prompt = USER_PROMPT_TEMPLATE.format(
        period_label=period_label,
        report_date=report_date,
        format_excerpt=format_excerpt or "(образец недоступен)",
        kallanish_block=kallanish_block,
        news_count=len(brief_input.news),
        news_block=news_block,
        user_tail=USER_PROMPT_TAIL_BY_KIND.get(brief_kind, USER_PROMPT_TAIL_BY_KIND["full"]),
    )
    return prompt, news_in_prompt


def resolve_max_items_in_prompt(total: int) -> int:
    if MAX_NEWS_IN_PROMPT <= 0:
        return total
    return min(total, MAX_NEWS_IN_PROMPT)


def body_char_limit_for_batch(total: int) -> int:
    if total <= 80:
        return NEWS_BODY_CHARS
    if total <= 200:
        return min(NEWS_BODY_CHARS, 700)
    return min(NEWS_BODY_CHARS, 450)


def format_news_block_grouped(
    news: Sequence[NewsRow | dict[str, Any]],
    max_items: int | None = None,
) -> tuple[str, int]:
    """Плоский нумерованный список новостей для user-промпта (без группировки по keyword_block)."""
    total = len(news)
    if max_items is None:
        max_items = resolve_max_items_in_prompt(total)
    body_limit = body_char_limit_for_batch(total)
    lines: list[str] = []
    for index, item in enumerate(news[:max_items], start=1):
        lines.append(format_single_news(index, item, body_limit))
        lines.append("")
    if total > max_items:
        lines.append(
            f"... ещё {total - max_items} новостей не переданы в промпт "
            f"(увеличьте BRIEF_MAX_NEWS_IN_PROMPT или сократите период)."
        )
    return ("\n".join(lines).strip() if lines else "(новостей нет)", min(total, max_items))


def format_single_news(
    index: int,
    item: NewsRow | dict[str, Any],
    body_limit: int | None = None,
) -> str:
    if isinstance(item, NewsRow):
        source = item.source
        title = item.title
        date_value = item.published_date.isoformat() if item.published_date else ""
        url = item.url
        body = item.content or item.summary
    else:
        source = item.get("source", "")
        title = item.get("title", "")
        date_value = item.get("date", "") or item.get("published_date", "")
        url = item.get("url", "")
        body = item.get("content") or item.get("summary", "")

    body = compact(body, body_limit or NEWS_BODY_CHARS)
    return (
        f"{index}. [{source}, {date_value}] {title}\n"
        f"URL: {url}\n"
        f"{body}"
    )


def compact(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def load_news_from_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("status", "ok") != "ok":
                continue
            if row.get("title") and row.get("url"):
                rows.append(row)
    return rows


def load_brief_input_from_db(
    database_url: str,
    period_range: PeriodRange,
    *,
    relevant_only: bool = False,
    keyword_block: str | None = None,
    limit: int = 2000,
) -> BriefInput:
    news = fetch_news_for_period(
        database_url,
        period_range.start,
        period_range.end,
        relevant_only=relevant_only,
        keyword_block=keyword_block,
        limit=limit,
    )
    return BriefInput(period_range=period_range, news=news)


def load_brief_input_from_jsonl(
    path: Path,
    period_range: PeriodRange,
    *,
    keyword_block: str | None = None,
    relevant_only: bool = False,
) -> BriefInput:
    rows = load_news_from_jsonl(path)
    if keyword_block:
        rows = [row for row in rows if row.get("keyword_block") == keyword_block]
    elif relevant_only:
        rows = [
            row
            for row in rows
            if row.get("keyword_block") or row.get("relevance_match") or row.get("keyword_match")
        ]
    return BriefInput(period_range=period_range, news=rows)


def load_brief_input_from_rag(
    database_url: str,
    period_range: PeriodRange,
    *,
    keyword_block: str | None = None,
    relevant_only: bool = False,
) -> BriefInput:
    """Новости для брифа из RAG-базы (rag_news_documents) с полным текстом."""
    from ..rag.vector_backend import fetch_news_documents_for_period

    docs = fetch_news_documents_for_period(
        database_url,
        period_start=period_range.start,
        period_end=period_range.end,
        keyword_block=keyword_block,
    )
    rows: list[dict[str, Any]] = [
        {
            "source": doc.source,
            "category": doc.category,
            "title": doc.title,
            "date": doc.news_date.strftime("%d.%m.%Y"),
            "url": doc.url,
            "summary": doc.summary,
            "content": doc.full_text,
            "language": "",
            "keyword_block": doc.keyword_block,
            "keyword_match": "",
            "relevance_match": "",
            "status": "ok",
        }
        for doc in docs
    ]
    if relevant_only and not keyword_block:
        filtered = [row for row in rows if row.get("keyword_block")]
        if filtered:
            rows = filtered
    return BriefInput(period_range=period_range, news=rows)
