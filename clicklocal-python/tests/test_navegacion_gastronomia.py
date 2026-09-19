from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import app


class ConsultaComercioFalsa:
    def __init__(self, comercio): self.comercio = comercio
    def select(self, *_): return self
    def eq(self, *_): return self
    def single(self): return self
    def execute(self): return SimpleNamespace(data=self.comercio)


class SupabaseFalso:
    def __init__(self, comercio): self.comercio = comercio
    def table(self, _): return ConsultaComercioFalsa(self.comercio)


class ConsultaPanelFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla

    def __getattr__(self, _nombre):
        return lambda *_args, **_kwargs: self

    def execute(self):
        if self.tabla == "comercios":
            return SimpleNamespace(data=[dict(self.db.comercio)])
        return SimpleNamespace(data=[])


class SupabasePanelFalso:
    def __init__(self, comercio):
        self.comercio = comercio

    def table(self, tabla):
        return ConsultaPanelFalsa(self, tabla)


def autenticar(categoria, next_destino=""):
    auth = SimpleNamespace(auth=SimpleNamespace(
        sign_in_with_password=lambda _: SimpleNamespace(user=SimpleNamespace(id="usuario-1"))
    ))
    comercio = {"id": "comercio-1", "activo": True, "categoria": categoria}
    with patch("app.supabase_auth", auth), patch("app.supabase_admin", SupabaseFalso(comercio)):
        return app.test_client().post(
            "/login",
            data={
                "email": "test@example.com",
                "password": "clave",
                "next": next_destino,
            },
        )


def test_login_gastronomico_entra_al_panel_gastronomico():
    respuesta = autenticar("Gastronomía")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/gastronomia/panel")


def test_login_no_gastronomico_conserva_panel_general():
    respuesta = autenticar("Comercio")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith(("/panel", "/panel.html"))


def test_panel_general_redirige_comercio_gastronomico_incluso_modo_general():
    comercio = {
        "id": "comercio-1",
        "user_id": "usuario-1",
        "activo": True,
        "categoria": "Gastronomía",
    }
    for ruta in ("/panel?modo=general", "/panel.html?modo=general"):
        cliente = app.test_client()

        with cliente.session_transaction() as sesion:
            sesion["user_id"] = "usuario-1"
            sesion["comercio"] = comercio

        with patch("app.supabase_admin", SupabasePanelFalso(comercio)):
            respuesta = cliente.get(ruta)

        assert respuesta.status_code == 302
        assert respuesta.location.endswith("/gastronomia/panel")


def test_panel_general_sigue_disponible_para_comercio_no_gastronomico():
    comercio = {
        "id": "comercio-2",
        "user_id": "usuario-2",
        "activo": True,
        "categoria": "Comercio",
    }
    cliente = app.test_client()

    with cliente.session_transaction() as sesion:
        sesion["user_id"] = "usuario-2"
        sesion["comercio"] = comercio

    with (
        patch("app.supabase_admin", SupabasePanelFalso(comercio)),
        patch("app.render_template", return_value="panel-general"),
    ):
        respuesta = cliente.get("/panel")

    assert respuesta.status_code == 200
    assert respuesta.get_data(as_text=True) == "panel-general"


def test_modo_gestor_gastronomico_redirige_sin_perder_contexto():
    comercio = {
        "id": "comercio-3",
        "user_id": "usuario-3",
        "activo": True,
        "categoria": "Gastronomia",
    }
    cliente = app.test_client()

    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = "comercio-3"

    with patch("app.supabase_admin", SupabasePanelFalso(comercio)):
        respuesta = cliente.get("/panel?gestor=1")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/gastronomia/panel")

    with cliente.session_transaction() as sesion:
        assert sesion["admin_logueado"] is True
        assert sesion["gestor_comercio_id"] == "comercio-3"


class ConsultaGastronomiaFalsa:
    def __init__(self, db, tabla):
        self.db = db
        self.tabla = tabla

    def __getattr__(self, _nombre):
        return lambda *_args, **_kwargs: self

    def execute(self):
        if self.tabla == "comercios":
            return SimpleNamespace(data=[dict(self.db.comercio)])
        if self.tabla == "gastronomia_configuracion":
            return SimpleNamespace(data=[{
                "comercio_id": self.db.comercio["id"],
                "activo": True,
                "acepta_delivery": False,
                "acepta_retiro": True,
                "pedido_minimo": 0,
                "costo_envio": 0,
                "tiempo_estimado_min": 30,
            }])
        return SimpleNamespace(data=[])


class SupabaseGastronomiaFalso:
    def __init__(self, comercio):
        self.comercio = comercio

    def table(self, tabla):
        return ConsultaGastronomiaFalsa(self, tabla)


def _comercio_gastronomico_terminos_pendientes(activo=True):
    return {
        "id": "comercio-gestor-1",
        "user_id": "propietario-1",
        "nombre_negocio": "Restaurante pendiente",
        "activo": activo,
        "categoria": "Gastronomía",
        "plan": "gratis",
        "terminos_aceptados_at": None,
        "terminos_version": None,
    }


def test_gestor_gastronomico_sin_terminos_entra_sin_modal_ni_login():
    comercio = _comercio_gastronomico_terminos_pendientes()
    cliente = app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = comercio["id"]
        sesion["comercio"] = comercio
        sesion["user_id"] = "sesion-admin-no-suplanta"

    with patch(
        "gastronomia.routes.supabase_admin",
        SupabaseGastronomiaFalso(comercio),
    ):
        respuesta = cliente.get("/gastronomia/panel")

    assert respuesta.status_code == 200
    assert b'id="modal-terminos"' not in respuesta.data
    with cliente.session_transaction() as sesion:
        assert sesion["user_id"] == "sesion-admin-no-suplanta"
        assert sesion["gestor_comercio_id"] == comercio["id"]
        assert sesion["comercio"]["terminos_aceptados_at"] is None


def test_propietario_gastronomico_sin_terminos_conserva_modal():
    comercio = _comercio_gastronomico_terminos_pendientes()
    cliente = app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["user_id"] = comercio["user_id"]
        sesion["comercio"] = comercio

    with patch(
        "gastronomia.routes.supabase_admin",
        SupabaseGastronomiaFalso(comercio),
    ):
        respuesta = cliente.get("/gastronomia/panel")

    assert respuesta.status_code == 200
    assert b'id="modal-terminos"' in respuesta.data


def test_gestor_gastronomico_no_exige_user_id_del_propietario():
    comercio = _comercio_gastronomico_terminos_pendientes()
    cliente = app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = comercio["id"]
        sesion["comercio"] = comercio

    with patch(
        "gastronomia.routes.supabase_admin",
        SupabaseGastronomiaFalso(comercio),
    ):
        respuesta = cliente.get("/gastronomia/panel")

    assert respuesta.status_code == 200
    with cliente.session_transaction() as sesion:
        assert "user_id" not in sesion
        assert sesion["gestor_comercio_id"] == comercio["id"]


def test_gestor_gastronomico_bloqueado_mantiene_proteccion():
    comercio = _comercio_gastronomico_terminos_pendientes(activo=False)
    cliente = app.test_client()
    with cliente.session_transaction() as sesion:
        sesion["admin_logueado"] = True
        sesion["gestor_comercio_id"] = comercio["id"]
        sesion["comercio"] = comercio

    with patch(
        "gastronomia.routes.supabase_admin",
        SupabaseGastronomiaFalso(comercio),
    ):
        respuesta = cliente.get("/gastronomia/panel")

    assert respuesta.status_code == 302
    assert respuesta.location.endswith(("/login", "/login.html"))


def test_contexto_gastronomia_compila_con_modo_gestor():
    app.jinja_env.get_template("gastronomia/panel.html")
    app.jinja_env.get_template("gastronomia/productos.html")


def test_login_con_destino_turnos_vuelve_a_la_agenda():
    respuesta = autenticar("Comercio", "/turnos/agenda")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/turnos/agenda")


def test_login_gastronomico_con_destino_turnos_vuelve_a_la_agenda():
    respuesta = autenticar("Gastronomía", "/turnos/agenda")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith("/turnos/agenda")


def test_login_descarta_next_externo():
    respuesta = autenticar("Comercio", "https://sitio-malicioso.test/")
    assert respuesta.status_code == 302
    assert respuesta.location.endswith(("/panel", "/panel.html"))


def test_login_get_preserva_unicamente_next_turnos():
    cliente = app.test_client()
    respuesta_valida = cliente.get("/login?next=/turnos/agenda")
    respuesta_invalida = cliente.get(
        "/login?next=https://sitio-malicioso.test/"
    )

    assert b'name="next" value="/turnos/agenda"' in respuesta_valida.data
    assert b'sitio-malicioso.test' not in respuesta_invalida.data


def test_templates_gastronomicos_ocultan_contenido_general_y_navegan():
    raiz = Path(__file__).resolve().parents[1] / "templates" / "gastronomia"
    for nombre in (
        "panel.html", "pos.html", "pedidos.html",
        "ventas.html", "pedidos_historial.html", "cierres_historial.html",
        "extras.html",
    ):
        texto = (raiz / nombre).read_text(encoding="utf-8")
        menu = texto.split(
            "{% block panel_menu_principal %}", 1
        )[1].split("{% endblock %}", 1)[0]

        assert "{% block panel_contenido_general %}{% endblock %}" in texto
        assert 'include "gastronomia/_panel_nav.html"' in menu
        assert "url_for('panel', modo='general')" not in texto

    nav = (raiz / "_panel_nav.html").read_text(encoding="utf-8")
    for etiqueta in (
        "Pedidos", "Ventas", "Mi negocio", "Menú y productos",
        "Configuración del negocio",
    ):
        assert etiqueta in nav
    assert ">Inicio<" not in nav
    assert "gastronomia.productos_gastronomia" in nav
    assert "gastronomia.configuracion_gastronomia" in nav
    assert "Ver menú público" in nav
    assert "gastro-nav-publico" in nav
    assert "aria-current=\"page\"" in nav

    panel_general = (raiz.parent / "panel.html").read_text(encoding="utf-8")
    assert "Volver a Gastronomía" in panel_general
    assert "{% block panel_contenido_general %}" in panel_general


def test_mi_negocio_separa_productos_y_configuracion_sin_pantalla_inicio():
    raiz = Path(__file__).resolve().parents[1]
    rutas = (raiz / "gastronomia" / "routes.py").read_text(encoding="utf-8")
    panel = (raiz / "templates" / "gastronomia" / "panel.html").read_text(encoding="utf-8")
    nav = (raiz / "templates" / "gastronomia" / "_panel_nav.html").read_text(encoding="utf-8")
    productos = (raiz / "templates" / "gastronomia" / "productos.html").read_text(encoding="utf-8")
    configuracion = (raiz / "templates" / "gastronomia" / "configuracion.html").read_text(encoding="utf-8")

    assert '@gastronomia_bp.route("/panel/productos", methods=["GET"])' in rutas
    assert '@gastronomia_bp.route("/panel/configuracion", methods=["GET"])' in rutas
    assert '@gastronomia_bp.route("/panel/plan", methods=["GET"])' in rutas
    assert '"gastronomia/inicio_panel.html"' not in rutas
    assert not (raiz / "templates" / "gastronomia" / "inicio_panel.html").exists()
    assert '"gastronomia/productos.html"' in rutas
    assert '"gastronomia/configuracion.html"' in rutas
    assert 'panel_seccion="productos"' in rutas
    assert '<details class="mi-negocio-nav"' in nav
    assert "panel_seccion in ('productos', 'configuracion', 'plan')" in nav
    assert "<summary>Mi negocio</summary>" in nav
    assert "Menú y productos" in panel
    assert "Configuración del negocio" in panel
    assert "gastronomia.plan_gastronomia" in nav
    assert "comercio.nombre_negocio" in panel
    assert '{% extends "gastronomia/panel.html" %}' in productos
    assert '{% extends "gastronomia/panel.html" %}' in configuracion
    assert "listas buscables" not in panel.lower()


def test_mi_negocio_tiene_lateral_vertical_y_botones_consistentes():
    raiz = Path(__file__).resolve().parents[1]
    nav = (raiz / "templates/gastronomia/_panel_nav.html").read_text(encoding="utf-8")
    panel = (raiz / "templates/gastronomia/panel.html").read_text(encoding="utf-8")
    css = (raiz / "static/gastronomia/panel.css").read_text(encoding="utf-8")

    assert 'class="mi-negocio-lateral"' in nav
    assert nav.index("Ver menú público") < nav.index("<summary>Pedidos</summary>") < nav.index("<summary>Ventas</summary>") < nav.index("<summary>Mi negocio</summary>")
    assert ".mi-negocio-lateral{display:grid" in css
    assert ".panel-menu>.mi-negocio-lateral{flex:0 0 100%" in css
    assert ".mi-negocio-btn--primario" in css
    assert ".mi-negocio-btn--secundario" in css
    assert ".mi-negocio-btn--chico" in css
    assert panel.count("mi-negocio-btn--primario") >= 3
    assert "Guardar ubicación de salida del delivery" in panel
    assert 'id="agregarFranjaDelivery" class="mi-negocio-btn mi-negocio-btn--chico"' in panel
    assert 'class="quitar-franja mi-negocio-btn mi-negocio-btn--chico"' in panel


def test_pedidos_y_ventas_usan_acordeones_sin_subnavegacion_duplicada():
    raiz = Path(__file__).resolve().parents[1] / "templates" / "gastronomia"
    pedidos = (raiz / "pedidos.html").read_text(encoding="utf-8")
    historial_pedidos = (raiz / "pedidos_historial.html").read_text(encoding="utf-8")
    ventas = (raiz / "ventas.html").read_text(encoding="utf-8")
    pos = (raiz / "pos.html").read_text(encoding="utf-8")
    cajas = (raiz / "cierres_historial.html").read_text(encoding="utf-8")

    assert "pedidos_vista = 'hoy'" in pedidos
    assert "pedidos_vista = 'historial'" in historial_pedidos
    assert "gastro-subnav" not in pedidos
    assert "gastro-subnav" not in historial_pedidos
    assert '<p class="titulo-chico">PEDIDOS</p>' in pedidos
    assert '<h2>Pedidos de hoy</h2>' in pedidos
    assert '<p class="titulo-chico">PEDIDOS</p>' in historial_pedidos
    assert '<h2>Historial de pedidos</h2>' in historial_pedidos
    assert "comercio.nombre_negocio" in pedidos
    assert "comercio.nombre_negocio" in historial_pedidos
    assert "ventas_vista = 'caja'" in ventas
    assert "ventas_vista = 'pos'" in pos
    assert "ventas_vista = 'historial'" in cajas
    for texto, titulo in (
        (ventas, "Caja actual"),
        (pos, "Punto de venta"),
        (cajas, "Historial de cajas"),
    ):
        assert "gastro-subnav" not in texto
        assert '<p class="titulo-chico">VENTAS</p>' in texto
        assert f"<h2>{titulo}</h2>" in texto
        assert "comercio.nombre_negocio" in texto
    assert "Nueva venta" not in pos


def test_acordeon_pedidos_tiene_destinos_y_estado_activo():
    raiz = Path(__file__).resolve().parents[1]
    nav = (raiz / "templates/gastronomia/_panel_nav.html").read_text(encoding="utf-8")
    css = (raiz / "static/gastronomia/panel.css").read_text(encoding="utf-8")

    assert '<details class="mi-negocio-nav pedidos-nav"{% if panel_seccion == \'pedidos\' %} open{% endif %}>' in nav
    assert '<summary>Pedidos</summary>' in nav
    assert "gastronomia.pedidos_gastronomia" in nav
    assert "gastronomia.historial_pedidos_gastronomia" in nav
    assert "pedidos_vista != 'historial'" in nav
    assert "pedidos_vista == 'historial'" in nav
    assert ".pedidos-nav" in css
    assert ".pedidos-seccion-encabezado" in css


def test_acordeon_ventas_tiene_destinos_y_estado_activo():
    raiz = Path(__file__).resolve().parents[1]
    nav = (raiz / "templates/gastronomia/_panel_nav.html").read_text(encoding="utf-8")
    css = (raiz / "static/gastronomia/panel.css").read_text(encoding="utf-8")

    assert '<details class="mi-negocio-nav ventas-nav"{% if panel_seccion == \'ventas\' %} open{% endif %}>' in nav
    assert "<summary>Ventas</summary>" in nav
    assert "gastronomia.ventas_gastronomia" in nav
    assert "gastronomia.pos_gastronomia" in nav
    assert "gastronomia.historial_cajas_gastronomia" in nav
    for vista in ("caja", "pos", "historial"):
        assert f"ventas_vista == '{vista}'" in nav
    assert ".ventas-nav" in css
    assert ".ventas-seccion-encabezado" in css
