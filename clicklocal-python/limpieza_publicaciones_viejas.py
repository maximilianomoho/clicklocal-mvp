from config.supabase_config import supabase_admin
import json
from pathlib import Path
from datetime import datetime

PUBLICACIONES_A_BORRAR = [
    "85acf87d-7576-4f11-a0a9-67c0e096fc15",
    "c580ca5f-3a39-47e5-a4ea-c9c8e7c8695d",
    "f5d09aca-6283-404c-88ad-5e76f55401bb",
    "a7d04b45-69cc-4d71-bc3b-2a9c69e5651a",
    "a0475767-d806-452c-bf5a-51fa61f62acd",
    "a9690610-2985-48d1-8378-b72958d57dc0",
    "0ff7b509-6da8-462a-a52a-aa905b9e5557",
    "a2082388-19c4-490f-8a51-05e46de7ca38",
    "b67a7092-69e4-4acf-9fdc-1afccdf1435d",
    "01fe7a6f-00aa-4975-9b24-5ec405b1d07f",
    "86b4d59c-0f72-42b3-84d2-74ad08c42feb",
    "7be0b5fa-c5e0-47c9-818b-587d368818bf",
    "9abf66f6-f06d-45e7-8943-e3f5ecda7eb8",
]

def extraer_ruta_storage(url):
    if not url:
        return None

    marcador = "/storage/v1/object/public/publicaciones/"
    if marcador not in url:
        return None

    ruta = url.split(marcador, 1)[1].split("?", 1)[0].strip()
    return ruta or None

res = (
    supabase_admin
    .table("publicaciones")
    .select("id,nombre,activa,comercio_id,imagen_url,imagen_principal,imagenes,created_at")
    .in_("id", PUBLICACIONES_A_BORRAR)
    .execute()
)

publicaciones = res.data or []

print("Publicaciones encontradas para borrar:", len(publicaciones))
print()

for pub in publicaciones:
    print("-", pub.get("nombre"), "|", pub.get("id"))

if len(publicaciones) != 13:
    print()
    print("FRENO: no se encontraron exactamente 13 publicaciones. No se borra nada.")
    raise SystemExit

backup_dir = Path("backups_limpieza")
backup_dir.mkdir(exist_ok=True)

backup_file = backup_dir / f"backup_publicaciones_viejas_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
backup_file.write_text(json.dumps(publicaciones, indent=2, ensure_ascii=False))

print()
print("Backup creado en:", backup_file)
print()
confirmacion = input("Para borrar definitivamente escribí BORRAR13 y Enter: ").strip()

if confirmacion != "BORRAR13":
    print("Cancelado. No se borró nada.")
    raise SystemExit

rutas_storage = set()

for pub in publicaciones:
    for campo in ["imagen_url", "imagen_principal"]:
        ruta = extraer_ruta_storage(pub.get(campo))
        if ruta:
            rutas_storage.add(ruta)

    imagenes = pub.get("imagenes") or []
    if isinstance(imagenes, list):
        for url in imagenes:
            ruta = extraer_ruta_storage(url)
            if ruta:
                rutas_storage.add(ruta)

print()
print("Borrando publicaciones de Supabase...")

delete_res = (
    supabase_admin
    .table("publicaciones")
    .delete()
    .in_("id", PUBLICACIONES_A_BORRAR)
    .execute()
)

print("Registros borrados:", len(delete_res.data or []))

if rutas_storage:
    print("Borrando imágenes de Storage:", len(rutas_storage))
    try:
        supabase_admin.storage.from_("publicaciones").remove(sorted(rutas_storage))
        print("Storage limpiado.")
    except Exception as e:
        print("ATENCIÓN: falló el borrado de algunas imágenes de Storage.")
        print(e)

print()
print("Limpieza terminada.")
