import os
from dotenv import load_dotenv
from supabase import create_client

# Cargar el .env de esta carpeta
load_dotenv(".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL:
    raise RuntimeError("Falta SUPABASE_URL en .env")

if not SUPABASE_ANON_KEY:
    raise RuntimeError("Falta SUPABASE_ANON_KEY en .env")

if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Falta SUPABASE_SERVICE_ROLE_KEY en .env")

# Cliente normal para registro/login
supabase_auth = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)

# Cliente servidor para escribir en tablas desde Flask
supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
