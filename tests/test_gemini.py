import os

from dotenv import load_dotenv
from google import genai


load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY не найден в .env")


client = genai.Client(api_key=api_key)


response = client.models.generate_content(
    model="gemini-3.7-flash",
    contents="Напиши короткое приветствие для Telegram-бота Таро."
)


print(response.text)