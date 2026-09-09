import os

from dotenv import load_dotenv


load_dotenv(".env")

TURNOS_APP_ORIGIN = os.getenv(
    "TURNOS_APP_ORIGIN",
    "https://turnos.clicklocal.com.ar",
).strip().rstrip("/")
