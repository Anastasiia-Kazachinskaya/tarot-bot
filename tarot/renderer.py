import uuid
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw, ImageFont

from .deck import get_card_image


CARD_WIDTH = 240
CARD_HEIGHT = 400
CARD_GAP = 30
PADDING = 40

BACKGROUND_COLOR = "white"

def get_font(size: int, bold: bool = False):
    font_names = (
        [
            # macOS
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            # Linux (Debian/Ubuntu, RHEL/Fedora)
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ]
        if bold
        else [
            # macOS
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            # Linux (Debian/Ubuntu, RHEL/Fedora)
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        ]
    )

    for font_name in font_names:
        if Path(font_name).exists():
            return ImageFont.truetype(font_name, size)

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        # Старые версии Pillow не поддерживают size у load_default.
        return ImageFont.load_default()

def render_spread(
    spread: list[dict],
    spread_name: str
) -> Path:
    """
    Собирает карты расклада в одно изображение.

    Поддерживаемые расклады:

    - one_card
    - three_cards
    - relationship
    - choice
    - seven_cards
    - celtic_cross

    Для перевёрнутых карт изображение
    поворачивается на 180 градусов.
    """

    cards = []

    # =================================================
    # Подготовка изображений карт
    # =================================================

    for item in spread:
        card = item["card"]

        image_path = get_card_image(card)

        image = Image.open(image_path).convert("RGB")

        image = ImageOps.contain(
            image,
            (CARD_WIDTH, CARD_HEIGHT)
        )

        if card["reversed"]:
            image = image.rotate(180)

        cards.append(image)

    count = len(cards)

    # =================================================
    # 1 КАРТА
    # =================================================

    if count == 1:
        width = (
            PADDING * 2
            + CARD_WIDTH
        )

        height = (
            PADDING * 2
            + CARD_HEIGHT
        )

        canvas = Image.new(
            "RGB",
            (width, height),
            BACKGROUND_COLOR
        )

        canvas.paste(
            cards[0],
            (PADDING, PADDING)
        )

    # =================================================
    # 3 КАРТЫ
    #
    # ┌───┐ ┌───┐ ┌───┐
    # │ 1 │ │ 2 │ │ 3 │
    # └───┘ └───┘ └───┘
    # =================================================

    elif count == 3:
        width = (
            PADDING * 2
            + CARD_WIDTH * 3
            + CARD_GAP * 2
        )

        height = (
            PADDING * 2
            + CARD_HEIGHT
        )

        canvas = Image.new(
            "RGB",
            (width, height),
            BACKGROUND_COLOR
        )

        x = PADDING

        for image in cards:
            canvas.paste(
                image,
                (x, PADDING)
            )

            x += CARD_WIDTH + CARD_GAP

    # =================================================
    # ОТНОШЕНИЯ
    #
    # ┌───┐ ┌───┐ ┌───┐
    # │ 1 │ │ 2 │ │ 3 │
    # └───┘ └───┘ └───┘
    #
    #     ┌───┐ ┌───┐
    #     │ 4 │ │ 5 │
    #     └───┘ └───┘
    # =================================================

    elif spread_name == "relationship":
        width = (
            PADDING * 2
            + CARD_WIDTH * 3
            + CARD_GAP * 2
        )

        height = (
            PADDING * 2
            + CARD_HEIGHT * 2
            + CARD_GAP
        )

        canvas = Image.new(
            "RGB",
            (width, height),
            BACKGROUND_COLOR
        )

        # Верхний ряд
        x = PADDING

        for image in cards[:3]:
            canvas.paste(
                image,
                (x, PADDING)
            )

            x += CARD_WIDTH + CARD_GAP

        # Нижний ряд
        bottom_width = (
            CARD_WIDTH * 2
            + CARD_GAP
        )

        bottom_start_x = (
            width - bottom_width
        ) // 2

        y = (
            PADDING
            + CARD_HEIGHT
            + CARD_GAP
        )

        x = bottom_start_x

        for image in cards[3:5]:
            canvas.paste(
                image,
                (x, y)
            )

            x += CARD_WIDTH + CARD_GAP

    # =================================================
    # ВЫБОР
    #
    #                    СИТУАЦИЯ
    #                       [1]
    #
    #             ВАРИАНТ А      ВАРИАНТ Б
    #
    #                [2]            [4]
    #             потенциал      потенциал
    #
    #                [3]            [5]
    #                риск            риск
    #
    # =================================================

    elif spread_name == "choice":

        option_gap = 70
        situation_gap = 45
        row_gap = 28

        options_width = (
            CARD_WIDTH * 2
            + option_gap
        )

        width = (
            PADDING * 2
            + options_width
        )

        # Компактная высота:
        # заголовок + карта 1 + варианты + две строки карт
        height = (
            PADDING
            + 32
            + 15
            + CARD_HEIGHT
            + situation_gap
            + 30
            + 15
            + CARD_HEIGHT
            + row_gap
            + CARD_HEIGHT
            + 45
        )

        canvas = Image.new(
            "RGB",
            (width, height),
            BACKGROUND_COLOR
        )

        draw = ImageDraw.Draw(canvas)

        title_font = get_font(25, bold=True)
        small_font = get_font(16, bold=True)

        # -------------------------------------------------
        # Вспомогательная функция
        # -------------------------------------------------

        def draw_centered_text(
            text: str,
            center_x: int,
            y: int,
            font
        ):
            bbox = draw.textbbox(
                (0, 0),
                text,
                font=font
            )

            text_width = bbox[2] - bbox[0]

            draw.text(
                (
                    center_x - text_width // 2,
                    y
                ),
                text,
                fill="black",
                font=font
            )

        # =================================================
        # №1 — СИТУАЦИЯ
        # =================================================

        situation_center = width // 2

        draw_centered_text(
            "СИТУАЦИЯ",
            situation_center,
            PADDING,
            title_font
        )

        situation_y = (
            PADDING
            + 32
            + 15
        )

        situation_x = (
            width - CARD_WIDTH
        ) // 2

        canvas.paste(
            cards[0],
            (
                situation_x,
                situation_y
            )
        )

        # =================================================
        # ВАРИАНТ А / Б
        # =================================================

        options_label_y = (
            situation_y
            + CARD_HEIGHT
            + situation_gap
        )

        left_x = PADDING

        right_x = (
            PADDING
            + CARD_WIDTH
            + option_gap
        )

        left_center = (
            left_x
            + CARD_WIDTH // 2
        )

        right_center = (
            right_x
            + CARD_WIDTH // 2
        )

        draw_centered_text(
            "ВАРИАНТ А",
            left_center,
            options_label_y,
            title_font
        )

        draw_centered_text(
            "ВАРИАНТ Б",
            right_center,
            options_label_y,
            title_font
        )

        # =================================================
        # ПОТЕНЦИАЛ
        # =================================================

        potential_y = (
            options_label_y
            + 30
            + 15
        )

        canvas.paste(
            cards[1],
            (
                left_x,
                potential_y
            )
        )

        canvas.paste(
            cards[3],
            (
                right_x,
                potential_y
            )
        )

        draw_centered_text(
            "потенциал",
            left_center,
            potential_y + CARD_HEIGHT + 5,
            small_font
        )

        draw_centered_text(
            "потенциал",
            right_center,
            potential_y + CARD_HEIGHT + 5,
            small_font
        )

        # =================================================
        # РИСК
        # =================================================

        risk_y = (
            potential_y
            + CARD_HEIGHT
            + row_gap
            + 20
        )

        canvas.paste(
            cards[2],
            (
                left_x,
                risk_y
            )
        )

        canvas.paste(
            cards[4],
            (
                right_x,
                risk_y
            )
        )

        draw_centered_text(
            "риск",
            left_center,
            risk_y + CARD_HEIGHT + 5,
            small_font
        )

        draw_centered_text(
            "риск",
            right_center,
            risk_y + CARD_HEIGHT + 5,
            small_font
        )


    # =================================================
    # 7 КАРТ
    #
    # ┌───┐ ┌───┐ ┌───┐ ┌───┐
    # │ 1 │ │ 2 │ │ 3 │ │ 4 │
    # └───┘ └───┘ └───┘ └───┘
    #
    #     ┌───┐ ┌───┐ ┌───┐
    #     │ 5 │ │ 6 │ │ 7 │
    #     └───┘ └───┘ └───┘
    # =================================================

    elif count == 7:
        top_count = 4
        bottom_count = 3

        width = (
            PADDING * 2
            + CARD_WIDTH * top_count
            + CARD_GAP * (top_count - 1)
        )

        height = (
            PADDING * 2
            + CARD_HEIGHT * 2
            + CARD_GAP
        )

        canvas = Image.new(
            "RGB",
            (width, height),
            BACKGROUND_COLOR
        )

        # Верхний ряд
        x = PADDING

        for image in cards[:4]:
            canvas.paste(
                image,
                (x, PADDING)
            )

            x += CARD_WIDTH + CARD_GAP

        # Нижний ряд
        bottom_width = (
            CARD_WIDTH * bottom_count
            + CARD_GAP * (bottom_count - 1)
        )

        bottom_start_x = (
            width - bottom_width
        ) // 2

        y = (
            PADDING
            + CARD_HEIGHT
            + CARD_GAP
        )

        x = bottom_start_x

        for image in cards[4:]:
            canvas.paste(
                image,
                (x, y)
            )

            x += CARD_WIDTH + CARD_GAP

    # =================================================
    # КЕЛЬТСКИЙ КРЕСТ
    #
    #                         3
    #
    #                         │
    #
    #                  4 ─── 1 ─── 6
    #                       ╳
    #                      2
    #
    #                         │
    #
    #                         5
    #
    #
    #                              7
    #                              8
    #                              9
    #                             10
    #
    # 1 — Суть ситуации
    # 2 — Что препятствует или пересекает ситуацию
    # 3 — Осознанное
    # 4 — Прошлое
    # 5 — Возможное развитие
    # 6 — Ближайшее будущее
    # 7 — Позиция вопрошающего
    # 8 — Внешние обстоятельства
    # 9 — Надежды и страхи
    # 10 — Итог
    # =================================================

    elif spread_name == "celtic_cross":

        # -------------------------------------------------
        # Центральный крест занимает 3 × 3 позиции.
        # -------------------------------------------------

        cross_width = (
            CARD_WIDTH * 3
            + CARD_GAP * 2
        )

        cross_height = (
            CARD_HEIGHT * 3
            + CARD_GAP * 2
        )

        # -------------------------------------------------
        # Правая колонка уменьшенных карт
        # -------------------------------------------------

        column_card_width = int(
            CARD_WIDTH * 0.65
        )

        column_card_height = int(
            CARD_HEIGHT * 0.65
        )

        column_gap = 20

        column_height = (
            column_card_height * 4
            + column_gap * 3
        )

        # -------------------------------------------------
        # Общий canvas
        # -------------------------------------------------

        right_column_gap = 80

        width = (
            PADDING * 2
            + cross_width
            + right_column_gap
            + column_card_width
        )

        height = (
            PADDING * 2
            + cross_height
        )

        canvas = Image.new(
            "RGB",
            (width, height),
            BACKGROUND_COLOR
        )

        # =================================================
        # ЦЕНТР
        # =================================================

        center_x = (
            PADDING
            + CARD_WIDTH
            + CARD_GAP
        )

        center_y = (
            PADDING
            + CARD_HEIGHT
            + CARD_GAP
        )

        # -------------------------------------------------
        # №1 — Суть ситуации
        # -------------------------------------------------

        canvas.paste(
            cards[0],
            (
                center_x,
                center_y
            )
        )

        # -------------------------------------------------
        # №2 — Что препятствует / пересекает ситуацию
        #
        # Карта лежит горизонтально поверх №1.
        # -------------------------------------------------

        cross_card = cards[1].rotate(
            90,
            expand=True
        )

        cross_x = (
            center_x
            + (
                CARD_WIDTH
                - cross_card.width
            ) // 2
        )

        cross_y = (
            center_y
            + (
                CARD_HEIGHT
                - cross_card.height
            ) // 2
        )

        canvas.paste(
            cross_card,
            (
                cross_x,
                cross_y
            )
        )

        # =================================================
        # №3 — Осознанное
        #
        # Полностью над №1
        # =================================================

        canvas.paste(
            cards[2],
            (
                center_x,
                PADDING
            )
        )

        # =================================================
        # №4 — Прошлое
        #
        # Полностью слева от №1
        # =================================================

        canvas.paste(
            cards[3],
            (
                PADDING,
                center_y
            )
        )

        # =================================================
        # №5 — Возможное развитие
        #
        # ВАЖНО:
        # №5 находится НИЖЕ центральной карты.
        # Здесь уже нет наложения на №1.
        # =================================================

        card_5_y = (
            center_y
            + CARD_HEIGHT
            + CARD_GAP
        )

        canvas.paste(
            cards[4],
            (
                center_x,
                card_5_y
            )
        )

        # =================================================
        # №6 — Ближайшее будущее
        #
        # Полностью справа от №1
        # =================================================

        canvas.paste(
            cards[5],
            (
                center_x
                + CARD_WIDTH
                + CARD_GAP,
                center_y
            )
        )

        # =================================================
        # ПРАВАЯ КОЛОНКА
        #
        # №7 — Позиция вопрошающего
        # №8 — Внешние обстоятельства
        # №9 — Надежды и страхи
        # №10 — Итог
        # =================================================

        column_x = (
            PADDING
            + cross_width
            + right_column_gap
        )

        column_start_y = (
            PADDING
            + (
                cross_height
                - column_height
            ) // 2
        )

        for index, image in enumerate(cards[6:10]):

            resized = ImageOps.contain(
                image,
                (
                    column_card_width,
                    column_card_height
                )
            )

            x = (
                column_x
                + (
                    column_card_width
                    - resized.width
                ) // 2
            )

            y = (
                column_start_y
                + index * (
                    column_card_height
                    + column_gap
                )
            )

            canvas.paste(
                resized,
                (x, y)
            )

    # =================================================
    # Неизвестный расклад
    # =================================================

    else:
        raise ValueError(
            f"Неподдерживаемый расклад: {spread_name}"
        )

    return _save_render(canvas)


def _save_render(canvas: Image.Image) -> Path:
    """
    Сохраняет готовое изображение под уникальным именем, чтобы
    параллельные запросы разных пользователей не перезаписывали
    изображение друг друга.
    """

    output_path = (
        Path(__file__).parent
        / "rendered"
        / f"{uuid.uuid4().hex}.jpg"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    canvas.save(
        output_path,
        quality=95
    )

    return output_path


def render_single_card(card: dict) -> Path:
    """
    Рендерит одну карту саму по себе — используется для
    уточняющей карты, вытянутой дополнительно к раскладу.
    """

    image_path = get_card_image(card)

    image = Image.open(image_path).convert("RGB")

    image = ImageOps.contain(
        image,
        (CARD_WIDTH, CARD_HEIGHT)
    )

    if card["reversed"]:
        image = image.rotate(180)

    width = PADDING * 2 + CARD_WIDTH
    height = PADDING * 2 + CARD_HEIGHT

    canvas = Image.new(
        "RGB",
        (width, height),
        BACKGROUND_COLOR
    )

    canvas.paste(
        image,
        (PADDING, PADDING)
    )

    return _save_render(canvas)