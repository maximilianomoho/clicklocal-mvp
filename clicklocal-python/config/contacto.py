import os

from dotenv import load_dotenv


load_dotenv(".env")

CLICKLOCAL_WHATSAPP = os.getenv("CLICKLOCAL_WHATSAPP", "").strip()
