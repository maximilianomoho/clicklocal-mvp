from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def leer(ruta):
    return (ROOT / ruta).read_text(encoding="utf-8")


def test_control_global_aparece_en_todas_las_paginas_clickjuegos():
    for template in (
        "templates/juegos/index.html",
        "templates/juegos/circulo.html",
        "templates/juegos/reflejos.html",
    ):
        contenido = leer(template)
        assert 'data-clickjuegos-sonido' in contenido
        assert 'aria-pressed="true"' in contenido
        assert "🔊 Sonido" in contenido


def test_motor_persiste_preferencia_y_expone_api_comun_de_audio():
    motor = leer("static/juegos/motor.js")
    assert 'clickjuegos_sonido' in motor
    assert 'window.localStorage.getItem(STORAGE_KEY)' in motor
    assert 'window.localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off")' in motor
    assert 'window.AudioContext || window.webkitAudioContext' in motor
    for funcion in ("playReady", "playSuccess", "playError", "playRecord"):
        assert f"{funcion}()" in motor


def test_circulo_usa_sonido_solo_al_resultado_y_record():
    circulo = leer("static/juegos/circulo.js")
    assert "ClickJuegos.audio.unlock()" in circulo
    assert "ClickJuegos.audio.playSuccess()" in circulo
    assert "if (data.is_new_record) ClickJuegos.audio.playRecord()" in circulo
    bloque_dibujo = circulo[circulo.index('canvas.addEventListener("pointermove"'):circulo.index("async function finalizar")]
    assert "playSuccess" not in bloque_dibujo
    assert "playRecord" not in bloque_dibujo


def test_reflejos_integra_ready_error_resultado_y_record_sin_alterar_medicion():
    reflejos = leer("static/juegos/reflejos.js")
    assert "ClickJuegos.audio.playReady()" in reflejos
    assert "ClickJuegos.audio.playError()" in reflejos
    assert "ClickJuegos.audio.playSuccess()" in reflejos
    assert "if (data.is_new_record) ClickJuegos.audio.playRecord()" in reflejos
    assert "const reactionMs = performance.now() - stimulusStartedAt;" in reflejos
    inicio_estimulo = reflejos.index("stimulusStartedAt = performance.now();")
    sonido_ready = reflejos.index("ClickJuegos.audio.playReady();")
    assert inicio_estimulo < sonido_ready


def test_portada_carga_motor_comun_sin_assets_externos_de_audio():
    portada = leer("templates/juegos/index.html")
    motor = leer("static/juegos/motor.js")
    assert "juegos/motor.js" in portada
    assert "new Audio(" not in motor
    assert "http://" not in motor and "https://" not in motor
