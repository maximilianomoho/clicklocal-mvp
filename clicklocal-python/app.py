from flask import Flask, render_template, send_from_directory, request, redirect, url_for, session
from werkzeug.utils import secure_filename
import os
import uuid
from supabase_config import supabase_auth, supabase_admin

app = Flask(__name__)

# Clave temporal para session en desarrollo local
app.secret_key = os.getenv("FLASK_SECRET_KEY", "clicklocal-mvp-dev")

# Carpeta donde guardamos fotos subidas en esta etapa local
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def comercio_default():
    return {
        "nombre_negocio": "Deck Bazar",
        "email": "deckbazar@test.com",
        "whatsapp": "3430000000",
        "direccion": "",
        "direccion_mostrar": "Venta Online",
        "venta_online": True,
        "ciudad": "Paraná",
        "categoria": "Hogar",
        "descripcion": "Bazar, regalos, mates y productos para el hogar.",
        "plan": "gratis",
    }


# INICIO / PLATAFORMA
@app.route("/")
@app.route("/index.html")
def inicio():
    comercio = session.get("comercio") or comercio_default()
    publicaciones = session.get("publicaciones", [])

    publicaciones_activas = []
    for pub in publicaciones:
        if pub.get("activa"):
            publicaciones_activas.append(pub)

    return render_template(
        "index.html",
        comercio=comercio,
        publicaciones=publicaciones_activas
    )


# REGISTRO COMERCIO
@app.route("/registro", methods=["GET", "POST"])
@app.route("/registro.html", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre_negocio = request.form.get("nombre_negocio", "").strip()
        email = request.form.get("email", "").strip().lower()
        whatsapp = request.form.get("whatsapp", "").strip()
        direccion = request.form.get("direccion", "").strip()
        venta_online = request.form.get("venta_online") == "on"
        ciudad = request.form.get("ciudad", "Paraná").strip()
        categoria = request.form.get("categoria", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        password = request.form.get("password", "").strip()
        repetir_password = request.form.get("repetir_password", "").strip()

        if not nombre_negocio or not email or not password:
            return "Faltan datos obligatorios: nombre del negocio, email o contraseña.", 400

        if password != repetir_password:
            return "Las contraseñas no coinciden.", 400

        if len(password) < 6:
            return "La contraseña debe tener al menos 6 caracteres.", 400

        direccion_mostrar = direccion if direccion else "Venta Online"

        try:
            auth_res = supabase_admin.auth.admin.create_user({
                "email": email,
                "password": password,
                "email_confirm": True
            })

            user = auth_res.user

            if not user:
                return "No se pudo crear el usuario en Supabase Auth.", 400

            comercio_nuevo = {
                "user_id": user.id,
                "nombre_negocio": nombre_negocio,
                "email": email,
                "whatsapp": whatsapp,
                "direccion": direccion,
                "direccion_mostrar": direccion_mostrar,
                "venta_online": venta_online,
                "ciudad": ciudad,
                "categoria": categoria,
                "descripcion": descripcion,
                "plan": "gratis",
            }

            insert_res = supabase_admin.table("comercios").insert(comercio_nuevo).execute()

            comercio_guardado = insert_res.data[0] if insert_res.data else comercio_nuevo

            session["user_id"] = user.id
            session["comercio"] = comercio_guardado
            session["publicaciones"] = []

            return redirect(url_for("panel"))

        except Exception as e:
            return f"Error registrando comercio: {e}", 400

    return render_template("registro.html")


# LOGIN COMERCIO
@app.route("/login", methods=["GET", "POST"])
@app.route("/login.html", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not email or not password:
            return "Falta email o contraseña.", 400

        try:
            auth_res = supabase_auth.auth.sign_in_with_password({
                "email": email,
                "password": password
            })

            user = auth_res.user

            if not user:
                return "No se pudo iniciar sesión.", 400

            comercio_res = (
                supabase_admin
                .table("comercios")
                .select("*")
                .eq("user_id", user.id)
                .single()
                .execute()
            )

            comercio = comercio_res.data

            if not comercio:
                return "Usuario válido, pero no se encontró comercio asociado.", 404

            session["user_id"] = user.id
            session["comercio"] = comercio
            session["publicaciones"] = []

            return redirect(url_for("panel"))

        except Exception as e:
            return f"Error iniciando sesión: {e}", 400

    return render_template("login.html")


# PANEL DEL COMERCIO
@app.route("/panel", methods=["GET", "POST"])
@app.route("/panel.html", methods=["GET", "POST"])
def panel():
    comercio = session.get("comercio") or comercio_default()
    publicaciones = session.get("publicaciones", [])

    if request.method == "POST":
        nombre = request.form.get("nombre_publicacion", "").strip()
        precio = request.form.get("precio", "").strip()
        descripcion = request.form.get("descripcion_publicacion", "").strip()
        activa = request.form.get("activa") == "on"

        if not nombre:
            return render_template(
                "panel.html",
                comercio=comercio,
                publicaciones=publicaciones,
                error="Falta el nombre de la publicación."
            )

        imagen_url = ""

        archivo = request.files.get("foto")
        if archivo and archivo.filename:
            nombre_seguro = secure_filename(archivo.filename)
            extension = os.path.splitext(nombre_seguro)[1].lower()
            nombre_final = f"{uuid.uuid4().hex}{extension}"
            ruta_final = os.path.join(UPLOAD_FOLDER, nombre_final)
            archivo.save(ruta_final)
            imagen_url = f"/static/uploads/{nombre_final}"

        nueva_publicacion = {
            "id": uuid.uuid4().hex,
            "nombre": nombre,
            "precio": precio,
            "descripcion": descripcion,
            "imagen_url": imagen_url,
            "activa": activa,
            "comercio": comercio["nombre_negocio"],
            "direccion_mostrar": comercio["direccion_mostrar"],
        }

        publicaciones.insert(0, nueva_publicacion)
        session["publicaciones"] = publicaciones
        session.modified = True

        return redirect(url_for("panel"))

    return render_template(
        "panel.html",
        comercio=comercio,
        publicaciones=publicaciones
    )


# DETALLE DE PUBLICACIÓN
@app.route("/detalle")
@app.route("/detalle.html")
def detalle_sin_id():
    publicaciones = session.get("publicaciones", [])

    if publicaciones:
        primera_publicacion = publicaciones[0]
        return redirect(url_for("detalle", publicacion_id=primera_publicacion["id"]))

    return redirect(url_for("inicio"))


@app.route("/detalle/<publicacion_id>")
def detalle(publicacion_id):
    comercio = session.get("comercio") or comercio_default()
    publicaciones = session.get("publicaciones", [])

    publicacion_encontrada = None

    for pub in publicaciones:
        if pub.get("id") == publicacion_id:
            publicacion_encontrada = pub
            break

    if not publicacion_encontrada:
        return redirect(url_for("inicio"))

    return render_template(
        "detalle.html",
        comercio=comercio,
        publicacion=publicacion_encontrada
    )


# PERFIL PÚBLICO DEL COMERCIO
@app.route("/perfil")
@app.route("/perfil.html")
def perfil():
    return render_template("perfil.html")


# PANEL DE CONTROL ADMIN
@app.route("/admin")
@app.route("/admin.html")
def admin():
    return render_template("admin.html")


# CSS
@app.route("/styles.css")
def styles():
    return send_from_directory("static", "styles.css")


# IMÁGENES DE LA MAQUETA
@app.route("/img/<path:filename>")
def imagenes(filename):
    return send_from_directory("static/img", filename)


if __name__ == "__main__":
    app.run(debug=True)