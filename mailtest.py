import os
import json
import urllib.request
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env")

api_key = os.environ["RESEND_API_KEY"]
data = json.dumps({
    "to": ["kohane@gmail.com"]
}).encode("utf-8")
req = urllib.request.Request(
    "https://api.resend.com/emails",
    data=data,
    method="POST",
    headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(req, timeout=10) as resp:
    print(resp.read().decode())
