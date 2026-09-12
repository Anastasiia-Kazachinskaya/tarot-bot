import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

client = OpenAI(
    api_key=os.getenv("CLOUD_API_KEY"),
    base_url="https://foundation-models.api.cloud.ru/v1",
    timeout=60.0
)


response = client.chat.completions.create(
    model="ai-sage/GigaChat3.5-432B-A28B",
    messages=[
        {
            "role": "user",
            "content": "Привет! Ответь одним коротким предложением."
        }
    ],
    max_completion_tokens=100,
    temperature=0.5,
    top_p=0.95,
)

print(response.choices[0].message.content)