"""Full-source hierarchical summary and question generation."""

from __future__ import annotations


class ContentGenerationError(RuntimeError):
    pass


def generate_full_summary(ai, transcript: str, segment_texts: list[str], budget: int) -> str:
    if ai.count_tokens(transcript) <= budget:
        return ai.summarize(transcript)
    groups = _group_complete_sources(ai, segment_texts, max(1, budget // 2))
    notes = [
        ai.summarize(_label_group(group, index, len(groups), "مصدر أصلي"))
        for index, group in enumerate(groups, 1)
    ]
    reduced = _reduce(ai, notes, budget, "ملاحظات وسيطة", ai.summarize)
    return ai.summarize(
        "أنشئ ملخصًا عالميًا متماسكًا يغطي كل الملاحظات التالية دون إسقاط أي قسم:\n\n"
        + reduced
    )


def generate_full_questions(ai, transcript: str, segment_texts: list[str], budget: int) -> str:
    if ai.count_tokens(transcript) <= budget:
        return ai.generate_questions(transcript)
    groups = _group_complete_sources(ai, segment_texts, max(1, budget // 2))
    candidates = [
        ai.generate_questions(_label_group(group, index, len(groups), "مصدر أصلي"))
        for index, group in enumerate(groups, 1)
    ]
    reduced = _reduce(ai, candidates, budget, "أسئلة مرشحة", ai.generate_questions)
    return ai.generate_questions(
        "اختر ونظّم وادمج الأسئلة التالية، وأزل التكرار مع الحفاظ على تغطية كل الأقسام:\n\n"
        + reduced
    )


def _reduce(ai, items: list[str], budget: int, label: str, transform) -> str:
    level = 0
    while ai.count_tokens("\n\n".join(items)) > budget:
        groups = _group_complete_sources(ai, items, max(1, budget // 2))
        if len(groups) >= len(items) and level >= 1:
            raise ContentGenerationError("Intermediate content cannot fit provider context")
        items = [
            transform(_label_group(group, index, len(groups), label))
            for index, group in enumerate(groups, 1)
        ]
        level += 1
        if level > 10:
            raise ContentGenerationError("Hierarchical content reduction exceeded safe depth")
    return "\n\n".join(
        f"[{label} {index}/{len(items)}]\n{item}" for index, item in enumerate(items, 1)
    )


def _group_complete_sources(ai, sources: list[str], budget: int) -> list[list[str]]:
    if not sources or any(not source.strip() for source in sources):
        raise ContentGenerationError("Every source section must be present and non-empty")
    groups: list[list[str]] = []
    current: list[str] = []
    for source in sources:
        candidate = [*current, source]
        if current and ai.count_tokens("\n\n".join(candidate)) > budget:
            groups.append(current)
            current = [source]
        else:
            current = candidate
    groups.append(current)
    return groups


def _label_group(group: list[str], index: int, total: int, label: str) -> str:
    sections = "\n\n".join(
        f"[{label} {section_index}/{len(group)}]\n{text}"
        for section_index, text in enumerate(group, 1)
    )
    return f"[مجموعة {index}/{total}]\n{sections}"
