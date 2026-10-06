"""Разделение брифового промпта на системную часть и блок структуры."""

from __future__ import annotations

import re

# Канонический маркер в шаблонах кода / при склейке кастомных промптов.
STRUCTURE_MARKER = "ОБЯЗАТЕЛЬНАЯ СТРУКТУРА"

# Короткие заголовки секции структуры в пользовательских дефолтах.
_STRUCTURE_TITLE_RE = re.compile(
    r"(?iu)^(?:#{1,3}\s*)?"
    r"(?:обязательная\s+)?"
    r"структура"
    r"(?:\s+(?:отч[её]та|брифа|дайджеста|отчёта))?"
    r"\s*:?\s*$"
)

# Нумерация разделов в кастомных промптах: «0. Глобальные правила», «1. Динамика цен…»
_NUMBERED_SECTION_RE = re.compile(r"^(?P<num>\d+)\.\s+\S.{0,78}$")


def _is_structure_marker_line(line: str) -> bool:
    """True для строки-заголовка блока структуры (не для упоминаний в правилах)."""
    stripped = (line or "").lstrip().strip()
    if not stripped:
        return False
    if stripped.startswith(STRUCTURE_MARKER):
        return True
    # Длинные правила («писать строго по структуре отчета…») не считаем маркером.
    if len(stripped) > 60:
        return False
    return bool(_STRUCTURE_TITLE_RE.match(stripped))


def _is_markdown_structure_heading(line: str) -> bool:
    stripped = (line or "").lstrip().strip()
    return stripped.startswith("## ")


def _find_numbered_structure_index(lines: list[str]) -> int | None:
    """Ищет старт структуры вида «0. …» / «1. …»."""
    candidates: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        stripped = line.lstrip().strip()
        if len(stripped) > 80:
            continue
        match = _NUMBERED_SECTION_RE.match(stripped)
        if not match:
            continue
        candidates.append((index, int(match.group("num"))))
    if not candidates:
        return None
    if len(candidates) == 1:
        index, num = candidates[0]
        # Один заголовок принимаем только если перед ним есть intro и номер 0/1.
        if index > 0 and num <= 1:
            return index
        return None
    for index, num in candidates:
        if num <= 1:
            return index
    return candidates[0][0]


def split_brief_prompt(text: str) -> dict[str, str]:
    """Делит промпт на system / user (структура).

    Порядок поиска границы:
    1) «ОБЯЗАТЕЛЬНАЯ СТРУКТУРА» / «Структура отчета» и короткие алиасы;
    2) первый markdown-заголовок ``## …``;
    3) нумерованные разделы «0. …» / «1. …» (кастомные промпты коллег).

    Если границы нет — весь текст в system_prompt, user_prompt пустой.
    """
    raw = text or ""
    lines = raw.splitlines(keepends=True)
    if not lines:
        return {"system_prompt": "", "user_prompt": ""}

    split_at: int | None = None
    for index, line in enumerate(lines):
        if _is_structure_marker_line(line):
            split_at = index
            break
    if split_at is None:
        for index, line in enumerate(lines):
            if index == 0:
                continue
            if _is_markdown_structure_heading(line):
                split_at = index
                break
    if split_at is None:
        split_at = _find_numbered_structure_index(lines)

    if split_at is None:
        return {"system_prompt": raw.strip(), "user_prompt": ""}

    system = "".join(lines[:split_at]).rstrip()
    structure = "".join(lines[split_at:]).strip()
    return {"system_prompt": system, "user_prompt": structure}


def join_brief_prompt(system_prompt: str | None, user_prompt: str | None) -> str:
    """Склеивает две половины; при необходимости добавляет маркер для roundtrip."""
    system = (system_prompt or "").strip()
    structure = (user_prompt or "").strip()
    if not structure:
        return system
    if not system:
        return structure

    first_line = structure.splitlines()[0] if structure else ""
    if (
        not _is_structure_marker_line(first_line)
        and not _is_markdown_structure_heading(first_line)
        and not _NUMBERED_SECTION_RE.match(first_line.lstrip().strip()[:90])
    ):
        # Пользователь вставил только разделы без заголовка — без маркера после
        # reload всё снова слиплось бы в system. Фиксируем канонический заголовок.
        structure = f"{STRUCTURE_MARKER}:\n{structure}"
    return f"{system}\n\n{structure}"
