from bot.tarot.deck import load_deck, get_card_image


deck = load_deck()

for card in deck:
    image = get_card_image(card)
    print(f"✓ {card['name']} → {image.name}")

print()
print(f"Проверено карт: {len(deck)}")