from dotenv import load_dotenv
load_dotenv()   
import os
from openai import OpenAI

client = OpenAI(
    base_url="https://aicredits.in/v1",
    api_key=os.environ["AICREDITS_API_KEY"],   # from env, never hardcoded 🔒
)

resp = client.chat.completions.create(
    model="deepseek-v4-flash",                 # ⬅️ use the EXACT model id from the AICredits catalog
    messages=[
        {"role": "system", "content": "You are a terse race engineer."},
        {"role": "user", "content": "Say 'radio check' and nothing else."},
    ],
)
print(resp.choices[0].message.content)