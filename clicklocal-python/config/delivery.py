import os


NOMINATIM_BASE_URL = (
    os.getenv("NOMINATIM_BASE_URL")
    or "https://nominatim.openstreetmap.org"
).rstrip("/")

OSRM_BASE_URL = (
    os.getenv("OSRM_BASE_URL")
    or "https://router.project-osrm.org"
).rstrip("/")

OSM_USER_AGENT = (
    os.getenv("OSM_USER_AGENT")
    or "ClickLocal-Gastronomia/1.0"
).strip()
