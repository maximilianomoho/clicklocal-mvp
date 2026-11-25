"""Vista previa visual aislada de Beneficios 2.0 con plantillas reales.

No importa app.py, no usa configuración externa y no abre conexiones.
Todo el estado existe sólo durante la vida de este proceso.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)


RAIZ = Path(__file__).resolve().parents[1]
STATIC = RAIZ / "static"
CSRF = "csrf-vista-previa-beneficios"
COMERCIO_ID = "10000000-0000-4000-8000-000000000001"
PUBLICACION_ID = "20000000-0000-4000-8000-000000000001"
CODIGO_MANUAL = "CASA-SOMB-2726-DEMO"
WHATSAPP_FICTICIO = "5493430000000"
TERMINOS_VERSION = "2026-07-22"


def _ahora_iso(dias=0):
    return (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()


def _oferta(
    oferta_id,
    etiqueta,
    tipo,
    valor,
    *,
    activa=True,
    edicion=1,
    serie_id=None,
    titulo=None,
    detalle_promocion=None,
):
    return {
        "id": oferta_id,
        "serie_id": serie_id or oferta_id,
        "numero_edicion": edicion,
        "publicacion_id": PUBLICACION_ID,
        "titulo": titulo or etiqueta,
        "detalle_promocion": detalle_promocion,
        "tipo_descuento": tipo,
        "valor_descuento": valor,
        "vigente_desde": _ahora_iso(-1),
        "vigente_hasta": _ahora_iso(30),
        "dias_semana": [0, 1, 2, 3, 4, 5, 6],
        "hora_desde": None,
        "hora_hasta": None,
        "cupo_maximo": 50,
        "maximo_unidades_codigo": 1,
        "mostrar_codigos_restantes": False,
        "tema_etiqueta": "clara",
        "metodos": ["whatsapp"],
        "activo": activa,
        "etiqueta": etiqueta,
    }


class EstadoDemo:
    def __init__(self):
        serie_editada = "30000000-0000-4000-8000-000000000001"
        self.ofertas = [
            _oferta(
                "30000000-0000-4000-8000-000000000002",
                "20% OFF",
                "porcentaje",
                "20",
                edicion=2,
                serie_id=serie_editada,
                titulo="20% OFF en Sombrero Panamá",
                detalle_promocion="Promo exclusiva en salón. No acumulable con otras promociones.",
            ),
            _oferta(
                "30000000-0000-4000-8000-000000000003",
                "2x1",
                "dos_por_uno",
                None,
                titulo="2x1 los viernes",
            ),
            _oferta(
                "30000000-0000-4000-8000-000000000004",
                "$ 5.000 OFF",
                "monto_fijo",
                "5000",
                activa=False,
                titulo="Oferta desactivada",
            ),
        ]
        self.historial = [
            _oferta(
                serie_editada,
                "15% OFF",
                "porcentaje",
                "15",
                activa=False,
                serie_id=serie_editada,
                titulo="Primera edición histórica",
            )
        ]
        self.codigos = {}
        self.codigo_manual_consumido = False

    def buscar(self, oferta_id):
        return next(
            (item for item in self.ofertas if item["id"] == oferta_id),
            None,
        )


def _etiqueta(datos):
    tipo = str(datos.get("tipo_descuento") or "")
    valor = datos.get("valor_descuento")
    fijas = {
        "dos_por_uno": "2x1",
        "tres_por_dos": "3x2",
        "segunda_unidad_50": "2ª AL 50%",
    }
    if tipo in fijas:
        return fijas[tipo]
    try:
        numero = Decimal(str(valor)).normalize()
        texto = format(numero, "f")
    except (InvalidOperation, TypeError, ValueError):
        texto = "0"
    if tipo == "porcentaje":
        return f"{texto}% OFF"
    if tipo == "monto_fijo":
        return f"$ {texto} OFF"
    return f"$ {texto}"


def _desde_payload(datos, *, oferta_id, serie_id=None, edicion=1):
    dias = datos.get("dias_semana") or list(range(7))
    etiqueta = _etiqueta(datos)
    return {
        "id": oferta_id,
        "serie_id": serie_id or oferta_id,
        "numero_edicion": edicion,
        "publicacion_id": PUBLICACION_ID,
        "titulo": str(datos.get("titulo") or etiqueta),
        "detalle_promocion": str(datos.get("detalle_promocion") or "").strip() or None,
        "tipo_descuento": str(datos.get("tipo_descuento") or "porcentaje"),
        "valor_descuento": datos.get("valor_descuento"),
        "vigente_desde": str(datos.get("vigente_desde") or _ahora_iso()),
        "vigente_hasta": str(datos.get("vigente_hasta") or _ahora_iso(7)),
        "dias_semana": [int(dia) for dia in dias],
        "hora_desde": datos.get("hora_desde"),
        "hora_hasta": datos.get("hora_hasta"),
        "cupo_maximo": datos.get("cupo_maximo"),
        "maximo_unidades_codigo": int(
            datos.get("maximo_unidades_codigo") or 1
        ),
        "mostrar_codigos_restantes": bool(
            datos.get("mostrar_codigos_restantes")
        ),
        "tema_etiqueta": str(datos.get("tema_etiqueta") or "clara"),
        "metodos": ["whatsapp"],
        "activo": True,
        "etiqueta": etiqueta,
    }


def _comercio():
    return {
        "id": COMERCIO_ID,
        "nombre_negocio": "La Casa del Sombrero — demostración ficticia",
        "nombre": "La Casa del Sombrero — demostración ficticia",
        "categoria": "Indumentaria",
        "ciudad": "Paraná",
        "direccion": "Peatonal ficticia 123",
        "direccion_mostrar": "Peatonal ficticia 123",
        "descripcion": "Comercio ficticio para revisar Beneficios 2.0.",
        "whatsapp": WHATSAPP_FICTICIO,
        "whatsapp_url": f"https://wa.me/{WHATSAPP_FICTICIO}",
        "activo": True,
        "logo_url": None,
        "terminos_version": TERMINOS_VERSION,
        "plan_actual": "gratis",
        "plan_nombre": "Gratis",
        "solicitud_premium": False,
        "fecha_vencimiento_plan_mostrar": None,
        "dias_restantes_plan": None,
    }


def _publicacion(estado):
    activa = next((o for o in estado.ofertas if o["activo"]), None)
    return {
        "id": PUBLICACION_ID,
        "nombre": "Sombrero Panamá clásico",
        "nombre_publicacion": "Sombrero Panamá clásico",
        "descripcion": "Sombrero liviano de ala media, ideal para el verano.",
        "precio": 45900,
        "activa": True,
        "imagen_principal": "/static/img/categorias/calzado-accesorios.webp",
        "imagen_url": "/static/img/categorias/calzado-accesorios.webp",
        "imagenes": [
            "/static/img/categorias/calzado-accesorios.webp",
            "/static/img/mate2.jpg",
        ],
        "beneficio_20_etiqueta": activa["etiqueta"] if activa else None,
        "metricas_visitas": 0,
        "metricas_whatsapp": 0,
        "metricas_conversion": None,
        "metricas_desde": "demostración",
    }


def _ofertas_publicas(estado):
    resultado = []
    for item in estado.ofertas:
        if not item["activo"]:
            continue
        beneficio = type("BeneficioVista", (), item)()
        condiciones = ["Válido todos los días", "Presentá tu código individual"]
        resultado.append({
            "beneficio": beneficio,
            "etiqueta": item["etiqueta"],
            "condiciones": condiciones,
        })
    return tuple(resultado)


def _ofertas_galeria(estado):
    ofertas = list(_ofertas_publicas(estado))
    for indice, oferta in enumerate(ofertas):
        oferta["indice_galeria"] = indice
    return tuple(ofertas)


def crear_aplicacion():
    app = Flask(
        __name__,
        template_folder=str(RAIZ / "templates"),
        static_folder=None,
    )
    app.secret_key = "clave-exclusiva-vista-previa-ficticia"
    app.config.update(
        PROPAGATE_EXCEPTIONS=True,
        SESSION_COOKIE_NAME="clicklocal_preview",
    )
    app.jinja_env.filters["precio_arg"] = lambda valor: (
        "$ " + format(Decimal(str(valor or 0)), ",.0f")
        .replace(",", ".")
    )
    estado = EstadoDemo()
    app.extensions["estado_beneficios_preview"] = estado

    def ruta_ajena_bloqueada(error, endpoint, values):
        del error, values
        return "/preview/bloqueado?destino=" + quote(endpoint, safe="")

    app.url_build_error_handlers.append(ruta_ajena_bloqueada)

    @app.after_request
    def asegurar_aislamiento(respuesta):
        respuesta.headers["Cache-Control"] = "no-store"
        respuesta.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "form-action 'self'; object-src 'none'; base-uri 'self'"
        )
        if respuesta.mimetype == "text/html":
            html = respuesta.get_data(as_text=True)
            aviso = """
            <div id="preview-aviso" style="position:sticky;top:0;z-index:100000;
              padding:10px 16px;background:#5b1720;color:#fff;text-align:center;
              font:800 14px Arial,sans-serif">VISTA PREVIA AISLADA · DATOS FICTICIOS · NO ES PRODUCCIÓN</div>
            <script>
            document.addEventListener("click", function (evento) {
              const enlace = evento.target.closest("a[href]");
              if (!enlace) return;
              const destino = new URL(enlace.href, window.location.href);
              if (destino.origin !== window.location.origin) {
                evento.preventDefault();
                window.alert("Enlace externo bloqueado en la vista previa aislada.");
              }
            }, true);
            document.addEventListener("submit", function (evento) {
              if (!evento.target.closest("#beneficios-20-formulario, #beneficios-20-canje-formulario")) {
                evento.preventDefault();
                window.alert("Operación ajena a Beneficios bloqueada en la demostración.");
              }
            }, true);
            </script>
            """
            respuesta.set_data(html.replace("<body>", "<body>" + aviso, 1))
        return respuesta

    @app.get("/styles.css")
    def estilos_clicklocal():
        return send_from_directory(STATIC, "styles.css")

    @app.get("/static/<path:filename>", endpoint="static")
    def recursos_estaticos(filename):
        if filename == "pwa.js":
            return Response(
                "/* Service worker deshabilitado en la vista previa. */",
                mimetype="application/javascript",
            )
        return send_from_directory(STATIC, filename)

    @app.get("/preview/panel", endpoint="panel")
    def vista_panel():
        session["preview"] = True
        return render_template(
            "panel.html",
            comercio=_comercio(),
            publicaciones=[_publicacion(estado)],
            csrf_beneficios=CSRF,
            TERMINOS_VERSION=TERMINOS_VERSION,
            CATEGORIAS_AUTOGESTION=["Indumentaria", "Otros"],
            CATEGORIA_CINE_TEATRO="Cines y teatros",
            modo_gestor=False,
            admin_user=None,
            es_premium=False,
            es_cine_teatro=False,
            historias=(),
            historias_activas=0,
            limite_historias_activas=2,
            listas_buscables=(),
            modulos_activos=(),
            modulos_asignados_limitados=(),
            modulos_disponibles=(),
            plan_nombre_panel="Gratis · demostración",
            historia_error=None,
            historia_ok=None,
        )

    @app.get("/preview/detalle")
    def vista_detalle():
        session["preview"] = True
        return render_template(
            "detalle.html",
            comercio=_comercio(),
            publicacion=_publicacion(estado),
            ofertas_beneficios=_ofertas_publicas(estado),
            csrf_beneficios=CSRF,
            regreso_galeria_url=url_for("panel"),
            publicacion_comprable=False,
            modulo_carrito_activo=False,
        )

    @app.get("/preview/galeria", endpoint="inicio")
    def vista_galeria():
        session["preview"] = True
        publicacion = _publicacion(estado)
        publicacion["comercio"] = _comercio()["nombre_negocio"]
        publicacion["comercio_id"] = COMERCIO_ID
        publicacion["direccion_mostrar"] = _comercio()["direccion_mostrar"]
        publicacion["tipo"] = "publicacion"
        publicacion["beneficio_20_indice"] = 0
        return render_template(
            "index.html",
            comercio=_comercio(), publicaciones=[publicacion],
            publicaciones_mas_vistas=[publicacion], comercios_relacionados=(),
            historias_publicas=(), comercios_para_descubrir=(), busqueda="",
            categoria_seleccionada="", macrocategorias=(), macro_slug="",
            macro_activa=type("Macro", (), {"nombre": "Comercios"})(),
            ofertas_beneficios_galeria=_ofertas_galeria(estado),
            csrf_beneficios=CSRF,
        )

    def vista_gastronomia():
        session["preview"] = True
        comercio = {
            **_comercio(), "imagen_portada": "/static/img/mate2.jpg",
            "busqueda_texto": "casa sombrero gastronomia", "acepta_delivery": True,
            "acepta_retiro": True, "tiempo_estimado_min": 30,
            "beneficio_20_etiqueta": estado.ofertas[0]["etiqueta"],
            "beneficio_20_indice": 0,
        }
        producto = {
            "id": PUBLICACION_ID, "comercio_id": COMERCIO_ID,
            "comercio_nombre": comercio["nombre_negocio"], "nombre": "Menú ficticio",
            "descripcion": "Producto gastronómico de demostración", "imagen_url": "/static/img/mate2.jpg",
            "precio_mostrar": "$ 12.000", "precio_anterior_mostrar": "$ 15.000",
            "disponible": True, "beneficio_20_etiqueta": estado.ofertas[0]["etiqueta"],
            "beneficio_20_indice": 0,
        }
        return render_template(
            "gastronomia/inicio.html", comercios_gastronomicos=[comercio],
            productos_busqueda=[producto], destacados_gastronomicos=[producto],
            promos_gastronomicas=[producto], busqueda="",
            ofertas_beneficios_galeria=_ofertas_galeria(estado), csrf_beneficios=CSRF,
        )

    app.add_url_rule(
        "/preview/gastronomia", endpoint="gastronomia.inicio",
        view_func=vista_gastronomia, methods=["GET"],
    )
    app.add_url_rule(
        "/preview/gastronomia/comercio/<comercio_id>",
        endpoint="gastronomia.comercio_gastronomico",
        view_func=lambda comercio_id: redirect(url_for("gastronomia.inicio")),
        methods=["GET"],
    )

    def autorizado():
        return request.headers.get("X-CSRF-Token") == CSRF

    @app.get("/panel/beneficios")
    def listar_beneficios():
        return jsonify(ok=True, beneficios=deepcopy(estado.ofertas))

    @app.post("/panel/publicaciones/<publicacion_id>/beneficios")
    def crear_beneficio(publicacion_id):
        if publicacion_id != PUBLICACION_ID:
            abort(404)
        if not autorizado():
            return jsonify(ok=False, mensaje="Solicitud no válida."), 403
        datos = request.get_json(silent=True) or {}
        creada = _desde_payload(datos, oferta_id=str(uuid4()))
        estado.ofertas.append(creada)
        return jsonify(ok=True, beneficio=deepcopy(creada)), 201

    @app.put("/panel/beneficios/<oferta_id>")
    def editar_beneficio(oferta_id):
        if not autorizado():
            return jsonify(ok=False, mensaje="Solicitud no válida."), 403
        anterior = estado.buscar(oferta_id)
        if anterior is None or not anterior["activo"]:
            return jsonify(ok=False, mensaje="Oferta no disponible."), 404
        datos = request.get_json(silent=True) or {}
        nueva = _desde_payload(
            datos,
            oferta_id=str(uuid4()),
            serie_id=anterior["serie_id"],
            edicion=anterior["numero_edicion"] + 1,
        )
        estado.historial.append({**anterior, "activo": False})
        estado.ofertas[estado.ofertas.index(anterior)] = nueva
        return jsonify(
            ok=True,
            beneficio=deepcopy(nueva),
            mensaje="Oferta actualizada sin alterar su historial.",
        )

    @app.post("/panel/beneficios/<oferta_id>/desactivar")
    def desactivar_beneficio(oferta_id):
        if not autorizado():
            return jsonify(ok=False, mensaje="Solicitud no válida."), 403
        oferta = estado.buscar(oferta_id)
        if oferta is None or not oferta["activo"]:
            return jsonify(ok=False, mensaje="Oferta no disponible."), 404
        oferta["activo"] = False
        return jsonify(
            ok=True,
            beneficio=deepcopy(oferta),
            mensaje="Oferta desactivada.",
        )

    @app.post("/beneficios/<oferta_id>/codigo", endpoint="beneficios_publico.obtener_codigo")
    def obtener_codigo(oferta_id):
        if not autorizado():
            return jsonify(ok=False, mensaje="Solicitud no válida."), 403
        oferta = estado.buscar(oferta_id)
        if oferta is None or not oferta["activo"]:
            return jsonify(ok=False, mensaje="Beneficio no disponible."), 409
        codigo = estado.codigos.setdefault(
            oferta_id,
            "CASA-SOMB-2726-DEMO",
        )
        mensaje = quote(
            f"Hola, quiero usar {oferta['titulo']}. Mi código es {codigo}."
        )
        return jsonify(
            ok=True,
            codigo=codigo,
            vence_en=_ahora_iso(1),
            titulo=oferta["titulo"],
            whatsapp_url=f"https://wa.me/{WHATSAPP_FICTICIO}?text={mensaje}",
            permite_presentar=True,
        )

    @app.post("/panel/beneficios/codigos/validar")
    def validar_codigo():
        if not autorizado():
            return jsonify(ok=False, mensaje="Solicitud no válida."), 403
        codigo = str((request.get_json(silent=True) or {}).get("codigo") or "").upper()
        if codigo != CODIGO_MANUAL or estado.codigo_manual_consumido:
            return jsonify(
                ok=False,
                mensaje="El código no existe, venció, ya fue utilizado o no pertenece a este comercio.",
            ), 404
        return jsonify(
            ok=True,
            mensaje="Cupón válido. Todavía no fue utilizado.",
            cupon={
                "beneficio_id": estado.ofertas[0]["id"],
                "titulo": estado.ofertas[0]["titulo"],
                "emitido_en": _ahora_iso(),
                "vence_en": _ahora_iso(1),
                "utilizado_en": None,
                "estado": "disponible",
            },
        )

    @app.post("/panel/beneficios/codigos/consumir")
    def consumir_codigo():
        if not autorizado():
            return jsonify(ok=False, mensaje="Solicitud no válida."), 403
        codigo = str((request.get_json(silent=True) or {}).get("codigo") or "").upper()
        if codigo != CODIGO_MANUAL or estado.codigo_manual_consumido:
            return jsonify(
                ok=False,
                mensaje="El código no existe, venció, ya fue utilizado o no pertenece a este comercio.",
            ), 409
        estado.codigo_manual_consumido = True
        return jsonify(
            ok=True,
            mensaje="Cupón marcado como utilizado. Esta acción no registra una venta.",
            cupon={
                "beneficio_id": estado.ofertas[0]["id"],
                "titulo": estado.ofertas[0]["titulo"],
                "emitido_en": _ahora_iso(),
                "vence_en": _ahora_iso(1),
                "utilizado_en": _ahora_iso(),
                "estado": "utilizado",
            },
        )

    @app.get("/imagen-compartir/<publicacion_id>", endpoint="imagen_compartir_publicacion")
    def imagen_compartir(publicacion_id):
        if publicacion_id != PUBLICACION_ID:
            abort(404)
        return send_from_directory(
            STATIC / "img" / "categorias",
            "calzado-accesorios.webp",
        )

    @app.get("/preview/comercio/<comercio_id>", endpoint="perfil_comercio")
    def perfil_ficticio(comercio_id):
        if comercio_id != COMERCIO_ID:
            abort(404)
        return redirect(url_for("vista_detalle"))

    rutas_bloqueadas = {
        "eliminar_publicacion": "/preview/bloqueado/publicacion/<publicacion_id>",
        "subir_logo_negocio": "/preview/bloqueado/subir-logo",
        "quitar_logo_negocio": "/preview/bloqueado/quitar-logo",
        "historial_historias_panel": "/preview/bloqueado/historias",
        "crear_historia_panel": "/preview/bloqueado/historias/crear",
        "admin_salir_gestor": "/preview/bloqueado/admin",
        "herramienta_detalle": "/preview/bloqueado/modulo/<slug>",
        "solicitar_instalacion_modulo_panel": "/preview/bloqueado/modulo/<slug>/solicitar",
    }

    def bloqueada(**_valores):
        return jsonify(
            ok=False,
            mensaje="Operación bloqueada en la vista previa aislada.",
        ), 403

    for endpoint, regla in rutas_bloqueadas.items():
        app.add_url_rule(
            regla,
            endpoint=endpoint,
            view_func=bloqueada,
            methods=["GET", "POST"],
        )

    return app


def ejecutar_autoprueba(app):
    cliente = app.test_client()
    cabeceras = {"X-CSRF-Token": CSRF}

    panel = cliente.get("/preview/panel")
    detalle = cliente.get("/preview/detalle")
    galeria = cliente.get("/preview/galeria")
    gastronomia = cliente.get("/preview/gastronomia")
    assert panel.status_code == 200
    assert detalle.status_code == 200
    assert galeria.status_code == 200
    assert gastronomia.status_code == 200
    assert b"La Casa del Sombrero" in panel.data
    assert b"La Casa del Sombrero" in detalle.data
    assert b"VISTA PREVIA AISLADA" in panel.data
    assert b"beneficios-20-formulario" in panel.data
    assert b"beneficios-20-publico" in detalle.data
    assert b"data-beneficios-galeria-indice" in galeria.data
    assert b"data-beneficios-galeria-indice" in gastronomia.data
    assert b"Service worker deshabilitado" in cliente.get("/static/pwa.js").data

    listado = cliente.get("/panel/beneficios")
    assert listado.status_code == 200
    assert len(listado.get_json()["beneficios"]) == 3

    payload = {
        "titulo": "3x2",
        "tipo_descuento": "tres_por_dos",
        "valor_descuento": None,
        "vigente_desde": _ahora_iso(),
        "vigente_hasta": _ahora_iso(7),
        "dias_semana": list(range(7)),
        "maximo_unidades_codigo": 1,
        "tema_etiqueta": "clara",
    }
    creada = cliente.post(
        f"/panel/publicaciones/{PUBLICACION_ID}/beneficios",
        json=payload,
        headers=cabeceras,
    )
    assert creada.status_code == 201
    creada_id = creada.get_json()["beneficio"]["id"]
    editada = cliente.put(
        f"/panel/beneficios/{creada_id}",
        json={**payload, "tipo_descuento": "porcentaje", "valor_descuento": "30"},
        headers=cabeceras,
    )
    assert editada.status_code == 200
    editada_id = editada.get_json()["beneficio"]["id"]
    assert editada.get_json()["beneficio"]["numero_edicion"] == 2
    desactivada = cliente.post(
        f"/panel/beneficios/{editada_id}/desactivar",
        headers=cabeceras,
    )
    assert desactivada.status_code == 200

    oferta_id = listado.get_json()["beneficios"][0]["id"]
    primera = cliente.post(f"/beneficios/{oferta_id}/codigo", headers=cabeceras)
    segunda = cliente.post(f"/beneficios/{oferta_id}/codigo", headers=cabeceras)
    assert primera.status_code == segunda.status_code == 200
    assert primera.get_json()["codigo"] == segunda.get_json()["codigo"]
    assert primera.get_json()["permite_presentar"] is True

    validada = cliente.post(
        "/panel/beneficios/codigos/validar",
        json={"codigo": CODIGO_MANUAL},
        headers=cabeceras,
    )
    assert validada.status_code == 200
    consumida = cliente.post(
        "/panel/beneficios/codigos/consumir",
        json={"codigo": CODIGO_MANUAL},
        headers=cabeceras,
    )
    repetida = cliente.post(
        "/panel/beneficios/codigos/consumir",
        json={"codigo": CODIGO_MANUAL},
        headers=cabeceras,
    )
    assert consumida.status_code == 200
    assert repetida.status_code == 409
    assert cliente.get("/ruta-ajena").status_code == 404
    print("preview_autoprueba=exitosa")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5058)
    parser.add_argument("--self-test", action="store_true")
    argumentos = parser.parse_args()
    if argumentos.host != "127.0.0.1":
        raise SystemExit("La vista previa sólo admite 127.0.0.1")
    app = crear_aplicacion()
    if argumentos.self_test:
        ejecutar_autoprueba(app)
        return
    app.run(
        host="127.0.0.1",
        port=argumentos.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
