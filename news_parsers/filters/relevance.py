"""Морфологический фильтр релевантности (prefix + suffix), как в steel_dozor."""

from __future__ import annotations

import re

import pymorphy3

from ..models import NewsItem, item_full_text

_morph = pymorphy3.MorphAnalyzer()

PREFIXES = [
    "строительство", "строить", "построить", "построен", "возводить", "возвести",
    "возведение", "сооружение", "сооружать",
    "планировать строительство", "планировать построить", "планировать возведение",
    "планировать создание", "планировать запуск", "планировать открытие",
    "собираться построить", "собираться строить",
    "намерен построить", "намерен строить",
    "инвестировать в строительство", "инвестировать в создание",
    "инвестиция в строительство", "инвестиция в создание",
    "инвестпроект", "вложить в строительство",
    "проект строительства", "проект нового", "проект создания",
    "проект возведения", "проект реконструкции", "проект модернизации",
    "запуск нового", "запуск производства", "ввод в эксплуатация",
    "открытие нового", "открыть новый",
    "реконструкция", "модернизация", "расширение",
    "капитальный ремонт", "техническое перевооружение",
    "начать строительство", "приступить к строительству",
    "заложить первый камень", "начало строительства",
    "анонсировать строительство", "объявить о строительстве",
    "подписать соглашение о строительстве", "соглашение о создании",
    "появиться новый", "создать новый", "создание нового",
]

SUFFIXES = [
    "завод", "фабрика", "комбинат", "производство", "предприятие", "цех",
    "металлургический завод", "сталелитейный завод",
    "нефтеперерабатывающий завод", "нпз", "химический завод",
    "цементный завод", "стекольный завод", "кирпичный завод",
    "машиностроительный завод", "автомобильный завод",
    "птицефабрика", "животноводческий комплекс", "агрокомплекс",
    "свинокомплекс", "молочная ферма", "тепличный комплекс", "теплица",
    "элеватор", "зернохранилище", "мясоперерабатывающий комбинат",
    "мясокомбинат", "молокозавод",
    "логистический комплекс", "логистический центр",
    "складской комплекс", "склад", "распределительный центр",
    "терминал", "грузовой терминал",
    "торговый центр", "трц", "торгово-развлекательный комплекс",
    "торговый комплекс", "гипермаркет",
    "бизнес-центр", "офисный центр", "многофункциональный комплекс",
    "бизнес-парк", "деловой центр",
    "стадион", "спортивный комплекс", "спорткомплекс", "арена",
    "ледовый дворец", "ледовая арена", "аквапарк", "бассейн",
    "мост", "путепровод", "тоннель", "эстакада", "развязка",
    "аэропорт", "аэровокзал", "вокзал",
    "порт", "причал", "пристань",
    "электростанция", "тэц", "тэс", "грэс", "подстанция",
    "котельная", "водозабор", "очистные сооружения",
    "жилой комплекс", "жк", "микрорайон", "жилой квартал",
    "технопарк", "индустриальный парк", "промышленный парк",
    "особая экономическая зона", "оэз", "кластер",
    "центр обработки данных", "цод", "дата-центр",
    "гостиница", "отель", "курорт", "санаторий",
    "больница", "поликлиника", "медицинский центр",
    "школа", "университет", "кампус",
    "ангар", "паркинг", "парковка",
    "выставочный комплекс", "конгресс-центр",
    "резиденция", "хаб",
]

DEFAULT_WINDOW_SIZE = 15

_prefix_norms: list[tuple[str, list[str]]] = []
_suffix_norms: list[tuple[str, list[str]]] = []
_window_size = DEFAULT_WINDOW_SIZE


def format_match(match: dict[str, str]) -> str:
    return f"{match['prefix']} + {match['suffix']}"


def find_relevance_matches(text: str, window_size: int | None = None) -> list[dict[str, str]]:
    global _window_size
    if window_size is not None:
        _window_size = window_size
    return _find_matches(text)


def filter_relevant_items(
    items: list[NewsItem],
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> tuple[list[NewsItem], dict[str, int]]:
    """Оставляет только релевантные ok-новости; диагностические записи сохраняет."""
    global _window_size
    _window_size = window_size

    kept: list[NewsItem] = []
    checked = 0
    passed = 0

    for item in items:
        if item.status != "ok":
            kept.append(item)
            continue
        checked += 1
        matches = find_relevance_matches(item_full_text(item), window_size=window_size)
        if not matches:
            continue
        passed += 1
        item.relevance_match = format_match(matches[0])
        kept.append(item)

    stats = {
        "checked": checked,
        "passed": passed,
        "dropped": checked - passed,
        "kept_total": len(kept),
    }
    return kept, stats


def _normalize_phrase(phrase: str) -> list[str]:
    words = phrase.lower().replace("ё", "е").split()
    result = []
    for word in words:
        parsed = _morph.parse(word)
        if parsed:
            result.append(parsed[0].normal_form)
        else:
            result.append(word)
    return result


def _init_norms() -> None:
    global _prefix_norms, _suffix_norms
    if _prefix_norms:
        return
    _prefix_norms = [(phrase, _normalize_phrase(phrase)) for phrase in PREFIXES]
    _suffix_norms = [(phrase, _normalize_phrase(phrase)) for phrase in SUFFIXES]


def _normalize_text_words(text: str) -> list[str]:
    words = re.findall(r"[а-яёa-z0-9-]+", text.lower().replace("ё", "е"))
    result = []
    for word in words:
        if len(word) < 2:
            continue
        parsed = _morph.parse(word)
        if parsed:
            result.append(parsed[0].normal_form)
        else:
            result.append(word)
    return result


def _find_matches(text: str) -> list[dict[str, str]]:
    _init_norms()
    words = _normalize_text_words(text)
    matches: list[dict[str, str]] = []

    for prefix_orig, prefix_norms in _prefix_norms:
        prefix_len = len(prefix_norms)
        for index in range(len(words) - prefix_len + 1):
            if not all(words[index + offset] == prefix_norms[offset] for offset in range(prefix_len)):
                continue
            search_start = index + prefix_len
            search_end = min(search_start + _window_size, len(words))
            for suffix_orig, suffix_norms in _suffix_norms:
                suffix_len = len(suffix_norms)
                for position in range(search_start, search_end - suffix_len + 1):
                    if all(words[position + offset] == suffix_norms[offset] for offset in range(suffix_len)):
                        matches.append({"prefix": prefix_orig, "suffix": suffix_orig})
                        break
    return matches
