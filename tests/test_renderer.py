from bot.tarot.deck import make_spread
from bot.tarot.renderer import render_spread


spread = make_spread(
    "celtic_cross",
    reversed_cards=True
)

image_path = render_spread(
    spread,
    "celtic_cross"
)

print(f"✓ Расклад собран: {image_path}")