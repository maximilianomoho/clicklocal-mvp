(() => {
  const canvas = document.getElementById("circuloLienzo");
  const ctx = canvas.getContext("2d");
  const estado = document.getElementById("circuloEstado");
  const resultado = document.getElementById("circuloResultado");
  const otraVez = document.getElementById("jugarOtraVez");
  let puntos = [], dibujando = false, nonce = null;
  let pointerActivo = null, resizeTimer = null;

  function ajustar() {
    const size = Math.min(canvas.parentElement.clientWidth, 520);
    const ratio = window.devicePixelRatio || 1;
    canvas.style.width = `${size}px`; canvas.style.height = `${size}px`;
    canvas.width = Math.round(size * ratio); canvas.height = Math.round(size * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.lineWidth = 5; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.strokeStyle = "#ff7a00";
  }

  async function preparar() {
    puntos = []; resultado.hidden = true; otraVez.hidden = true;
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    estado.hidden = false; estado.textContent = "Preparando partida...";
    try {
      const data = await ClickJuegos.json("/jugar/api/circulo/intentos", { method: "POST", body: "{}" });
      nonce = data.nonce; estado.textContent = "Dibujá cuando quieras";
    } catch (error) { estado.textContent = error.message; }
  }

  function punto(event) {
    const rect = canvas.getBoundingClientRect();
    return { px: event.clientX - rect.left, py: event.clientY - rect.top,
      normal: [(event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height] };
  }

  canvas.addEventListener("pointerdown", (event) => {
    ClickJuegos.audio.unlock();
    if (!nonce || dibujando) return;
    event.preventDefault(); canvas.setPointerCapture(event.pointerId);
    dibujando = true; pointerActivo = event.pointerId; puntos = [];
    ctx.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
    const p = punto(event); puntos.push(p.normal); ctx.beginPath(); ctx.moveTo(p.px, p.py); estado.textContent = "";
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dibujando) return;
    event.preventDefault(); const p = punto(event); const ultimo = puntos[puntos.length - 1];
    if (Math.hypot(p.normal[0] - ultimo[0], p.normal[1] - ultimo[1]) < 0.004) return;
    // Al llegar al límite se conserva una muestra uniforme de todo lo
    // dibujado y se continúa incorporando el tramo nuevo. El servidor evalúa
    // así el trazo completo, no solamente sus primeros 400 puntos.
    if (puntos.length >= 400) {
      puntos = puntos.filter((_, index) => index % 2 === 0);
    }
    puntos.push(p.normal);
    ctx.lineTo(p.px, p.py); ctx.stroke();
  });

  async function finalizar(event) {
    if (!dibujando) return;
    dibujando = false; pointerActivo = null;
    try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
    estado.hidden = false; estado.textContent = "Calculando...";
    const nonceActual = nonce; nonce = null;
    try {
      const puntosEnviados = puntos;
      puntos = [];
      const data = await ClickJuegos.json("/jugar/api/circulo/partidas", {
        method: "POST", body: JSON.stringify({ nonce: nonceActual, points: puntosEnviados }),
      });
      document.getElementById("circuloScore").textContent = ClickJuegos.score(data.score);
      document.getElementById("circuloMejor").textContent = ClickJuegos.score(data.personal_best);
      document.getElementById("circuloRecordMensaje").textContent = data.is_new_record ? "🔥 Nuevo récord personal" : "";
      document.getElementById("circuloPuesto").textContent = data.weekly_position ? `Puesto semanal: #${data.weekly_position}` : "";
      resultado.hidden = false; estado.hidden = true;
      ClickJuegos.audio.playSuccess();
      if (data.is_new_record) ClickJuegos.audio.playRecord();
    } catch (error) { estado.textContent = error.message; }
    otraVez.hidden = false;
  }
  canvas.addEventListener("pointerup", finalizar);
  canvas.addEventListener("pointercancel", finalizar);
  otraVez.addEventListener("click", preparar);

  const rankingPanel = document.getElementById("rankingPanel");
  async function cargarRanking(periodo = "semana") {
    rankingPanel.hidden = false;
    const lista = document.getElementById("rankingLista");
    const posicion = document.getElementById("rankingPosicion");
    lista.innerHTML = "<li>Cargando...</li>";
    try {
      const data = await ClickJuegos.json(`/jugar/api/circulo/ranking?periodo=${encodeURIComponent(periodo)}`);
      lista.replaceChildren(...data.entries.map((entry) => {
        const li = document.createElement("li"), nombre = document.createElement("span"), marca = document.createElement("strong");
        nombre.textContent = `#${entry.position} ${entry.alias}`; marca.textContent = ClickJuegos.score(entry.score);
        li.append(nombre, marca); return li;
      }));
      if (!data.entries.length) lista.innerHTML = "<li>Todavía no hay marcas para este período.</li>";
      posicion.textContent = data.player_position ? `Tu posición: #${data.player_position}` : "";
    } catch (error) { lista.innerHTML = ""; posicion.textContent = error.message; }
  }
  document.getElementById("verRanking").addEventListener("click", () => cargarRanking("semana"));
  document.querySelectorAll("[data-periodo]").forEach((boton) => boton.addEventListener("click", () => {
    document.querySelectorAll("[data-periodo]").forEach((b) => b.classList.toggle("activo", b === boton));
    cargarRanking(boton.dataset.periodo);
  }));
  document.getElementById("aliasForm").addEventListener("submit", async (event) => {
    event.preventDefault(); const estadoAlias = document.getElementById("aliasEstado");
    try {
      const data = await ClickJuegos.json("/jugar/api/jugador/alias", { method: "POST",
        body: JSON.stringify({ alias: document.getElementById("aliasInput").value }) });
      estadoAlias.textContent = `Nombre guardado: ${data.alias}`;
      await cargarRanking("semana");
      window.setTimeout(() => {
        document.getElementById("aliasPanel").hidden = true;
      }, 700);
    } catch (error) { estadoAlias.textContent = error.message; }
  });
  function cambioDeTamano() {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      const habiaTrazo = dibujando || puntos.length > 0;
      if (habiaTrazo) {
        dibujando = false;
        if (pointerActivo !== null) {
          try { canvas.releasePointerCapture(pointerActivo); } catch (_) {}
        }
        pointerActivo = null;
        puntos = [];
        nonce = null;
        ajustar();
        estado.hidden = false;
        estado.textContent = "El tamaño de pantalla cambió. Volvé a intentarlo.";
        window.setTimeout(preparar, 1200);
        return;
      }
      ajustar();
    }, 160);
  }

  ajustar(); window.addEventListener("resize", cambioDeTamano); preparar();
})();
