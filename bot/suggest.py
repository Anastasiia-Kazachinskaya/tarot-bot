"""
Подбор расклада по тексту вопроса — простые правила, без запроса к модели.
Ошибиться не страшно: пользователь видит предложение и может выбрать
другой формат одной кнопкой.
"""

import re

_CHOICE = re.compile(
    r"(\bили\b|\bвыбрать\b|\bвыбор\b|\bуходить ли\b|\bоставаться ли\b|"
    r"\bпереезжать ли\b|\bсоглашаться ли\b|\bпринимать ли\b)"
)
_RELATIONSHIP = re.compile(
    r"(отношени|\bпарн|\bмуж|\bжена\b|\bжены\b|\bжене\b|девушк|\bбывш|"
    r"любов|влюбл|свидан|\bон\b|\bона\b|\bнего\b|\bнеё\b|\bнее\b|\bним\b|"
    r"\bему\b|\bей\b|\bего\b|\bеё\b|\bее\b|\bим\b|"
    r"\bней\b|подруг|\bдруг[ауеом]?\b|\bмам|\bпап|сестр|\bбрат|коллег|"
    r"начальни|партн[её]р)"
)
_ADVICE = re.compile(
    r"(что (мне )?делать|как (мне )?быть|как поступить|как справиться|"
    r"\bсовет|что поможет|как (мне )?подойти)"
)
_FUTURE = re.compile(
    r"(ждёт|ждет|будет|завтра|на неделе|неделю|месяц|в этом году|"
    r"следующ|чем закончится|к чему)"
)
_DEEP = re.compile(r"(подробно|глубоко|целиком|всё про|все про|разобраться)")

REASONS = {
    "choice": "в вопросе есть выбор между вариантами",
    "relationship": "вопрос про другого человека и то, что между вами",
    "seven_cards": "вопрос «что делать» — здесь есть позиция «Совет»",
    "three_cards": "вопрос о том, как ситуация будет развиваться",
    "celtic_cross": "ситуация сложная, её стоит рассмотреть целиком",
    "one_card": "короткий вопрос — хватит одного фокуса",
}


def suggest_spread(question: str) -> tuple[str, str]:
    """Возвращает (ключ расклада, короткое объяснение)."""

    raw = question.lower()
    text = " ".join(raw.split())

    if _CHOICE.search(raw):
        spread = "choice"
    elif _RELATIONSHIP.search(raw):
        spread = "relationship"
    elif _ADVICE.search(raw):
        spread = "seven_cards"
    elif _DEEP.search(raw) or len(text) > 160:
        spread = "celtic_cross"
    elif _FUTURE.search(raw):
        spread = "three_cards"
    elif len(text) <= 45:
        spread = "one_card"
    else:
        spread = "three_cards"

    return spread, REASONS[spread]
