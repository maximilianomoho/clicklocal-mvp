(() => {
  "use strict";
  const modal = document.getElementById("beneficios-20-modal");
  if (!modal) return;
  const $ = (selector) => modal.querySelector(selector);
  const formulario = $("#beneficios-20-formulario");
  const tipo = $("#beneficios-20-tipo");
  const valor = $("#beneficios-20-valor");
  const campoValor = $("[data-campo-valor]");
  const etiqueta = $("#beneficios-20-etiqueta");
  const textoPromocion = $("#beneficios-20-texto-promocion");
  const inicio = $("#beneficios-20-inicio");
  const fin = $("#beneficios-20-fin");
  const inicioWrap = $("#beneficios-20-inicio-wrap");
  const diasPanel = $("#beneficios-20-dias");
  const horarioPanel = $("#beneficios-20-horario");
  const cupoWrap = $("#beneficios-20-cupo-wrap");
  const estado = $("#beneficios-20-estado");
  const botonGuardar = formulario.querySelector('button[type="submit"]');
  const gestion = document.getElementById("beneficios-20-gestion");
  const listaOfertas = document.getElementById("beneficios-20-lista");
  const estadoGestion = document.getElementById("beneficios-20-gestion-estado");
  let botonActual = null;
  let beneficioActual = null;
  let enviando = false;
  let duracion = 7;

  function objetivoDelBoton(boton) {
    if (!boton) return null;
    if (boton.dataset.gastronomiaProductoId) {
      return {
        tipo: "gastronomia_producto",
        id: boton.dataset.gastronomiaProductoId,
        rutaCreacion: "/panel/gastronomia/productos/" + encodeURIComponent(boton.dataset.gastronomiaProductoId) + "/beneficios"
      };
    }
    if (boton.dataset.publicacionId) {
      return {
        tipo: "publicacion",
        id: boton.dataset.publicacionId,
        rutaCreacion: "/panel/publicaciones/" + encodeURIComponent(boton.dataset.publicacionId) + "/beneficios"
      };
    }
    return null;
  }

  const iso = (fecha) => [fecha.getFullYear(), String(fecha.getMonth() + 1).padStart(2, "0"), String(fecha.getDate()).padStart(2, "0")].join("-");
  const visible = (fecha) => [String(fecha.getDate()).padStart(2, "0"), String(fecha.getMonth() + 1).padStart(2, "0"), fecha.getFullYear()].join("-");
  const leer = (texto) => {
    const partes = /^(\d{2})-(\d{2})-(\d{4})$/.exec(String(texto || "").trim());
    if (!partes) return null;
    const fecha = new Date(Number(partes[3]), Number(partes[2]) - 1, Number(partes[1]));
    return fecha.getFullYear() === Number(partes[3]) && fecha.getMonth() === Number(partes[2]) - 1 && fecha.getDate() === Number(partes[1]) ? fecha : null;
  };
  const visibleDesdeIso = (texto) => {
    const fecha = texto ? new Date(texto.slice(0, 10) + "T00:00:00") : null;
    return fecha && !Number.isNaN(fecha.getTime()) ? visible(fecha) : "";
  };
  const sumar = (fecha, dias) => { const copia = new Date(fecha); copia.setDate(copia.getDate() + dias); return copia; };
  const fechaConZona = (texto) => {
    const fecha = leer(texto);
    return fecha ? iso(fecha) + "T00:00:00-03:00" : "";
  };

  function sincronizarDias() {
    const todos = $('input[name="dias-modo"]:checked').value === "todos";
    diasPanel.hidden = todos;
    diasPanel.querySelectorAll("input").forEach((item) => {
      if (todos) item.checked = true;
      item.disabled = todos;
    });
  }

  function sincronizarHorario() {
    const todoElDia = $('input[name="horario-modo"]:checked').value === "todo";
    horarioPanel.hidden = todoElDia;
    horarioPanel.querySelectorAll("input").forEach((item) => { item.disabled = todoElDia; });
  }

  function actualizarPromocion() {
    const numero = Math.max(0, Number(valor.value) || 0);
    const textos = {
      porcentaje: numero + "% OFF", monto_fijo: "$" + numero.toLocaleString("es-AR") + " OFF",
      precio_especial: "$" + numero.toLocaleString("es-AR"), dos_por_uno: "2x1",
      tres_por_dos: "3x2", segunda_unidad_50: "2ª AL 50%"
    };
    campoValor.hidden = !["porcentaje", "monto_fijo", "precio_especial"].includes(tipo.value);
    $("#beneficios-20-unidad").textContent = tipo.value === "porcentaje" ? "%" : "$";
    valor.max = tipo.value === "porcentaje" ? "100" : "";
    textoPromocion.textContent = textos[tipo.value];
    etiqueta.textContent = textos[tipo.value];
  }

  function actualizarVigencia(recalcular = false) {
    const inmediato = $('input[name="inicio"]:checked').value === "inmediato";
    inicioWrap.hidden = inmediato;
    if (inmediato) inicio.value = visible(new Date());
    const desde = leer(inicio.value);
    if (!desde) return;
    if (recalcular || !fin.value) fin.value = visible(sumar(desde, duracion));
    const hasta = leer(fin.value);
    $("#beneficios-20-resumen-vigencia").textContent = hasta && hasta > desde
      ? "Finaliza el " + new Intl.DateTimeFormat("es-AR", { dateStyle: "long" }).format(hasta) + "."
      : "La finalización debe ser posterior al inicio.";
  }

  function prepararCreacion() {
    beneficioActual = null;
    formulario.reset();
    duracion = 7;
    inicio.value = visible(new Date());
    fin.value = "";
    diasPanel.hidden = true;
    horarioPanel.hidden = true;
    cupoWrap.hidden = true;
    sincronizarDias();
    sincronizarHorario();
    $("#beneficios-20-modal-titulo").textContent = "Configurar beneficio";
    botonGuardar.textContent = "Guardar beneficio";
    actualizarPromocion();
    actualizarVigencia(true);
  }

  function abrir(boton, conservarFormulario = false) {
    botonActual = boton;
    if (!conservarFormulario) prepararCreacion();
    $("#beneficios-20-producto").textContent = boton.dataset.producto || "Publicación";
    modal.hidden = false;
    document.body.classList.add("beneficios-20-modal-abierto");
    estado.textContent = "Configuración local aislada.";
    enviando = false;
    botonGuardar.disabled = false;
    $(".beneficios-20-modal__cerrar").focus();
  }
  function cerrar() {
    modal.hidden = true;
    document.body.classList.remove("beneficios-20-modal-abierto");
    if (botonActual) botonActual.focus();
  }

  document.querySelectorAll(".beneficios-20-abrir-modal").forEach((boton) => boton.addEventListener("click", () => abrir(boton)));
  modal.querySelectorAll("[data-cerrar-modal]").forEach((boton) => boton.addEventListener("click", cerrar));
  tipo.addEventListener("change", actualizarPromocion);
  valor.addEventListener("input", actualizarPromocion);
  modal.querySelectorAll('input[name="tema"]').forEach((radio) => radio.addEventListener("change", () => etiqueta.classList.toggle("oscura", radio.checked && radio.value === "oscura")));
  modal.querySelectorAll('input[name="inicio"]').forEach((radio) => radio.addEventListener("change", () => actualizarVigencia(true)));
  inicio.addEventListener("change", () => actualizarVigencia(true));
  fin.addEventListener("change", () => actualizarVigencia(false));
  modal.querySelectorAll("[data-dias]").forEach((boton) => boton.addEventListener("click", () => {
    duracion = Number(boton.dataset.dias);
    modal.querySelectorAll("[data-dias]").forEach((otro) => otro.classList.toggle("seleccionado", otro === boton));
    actualizarVigencia(true);
  }));
  modal.querySelectorAll('input[name="dias-modo"]').forEach((radio) => radio.addEventListener("change", sincronizarDias));
  modal.querySelectorAll('input[name="horario-modo"]').forEach((radio) => radio.addEventListener("change", sincronizarHorario));
  modal.querySelectorAll('input[name="cupo-modo"]').forEach((radio) => radio.addEventListener("change", () => { cupoWrap.hidden = !(radio.checked && radio.value === "limitado"); }));

  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    if (enviando) return;
    const objetivo = objetivoDelBoton(botonActual);
    if (!objetivo) {
      estado.textContent = "No se pudo identificar el producto."; return;
    }
    if (!inicio.value || !fin.value || leer(fin.value) <= leer(inicio.value)) {
      estado.textContent = "Revisá las fechas del beneficio."; return;
    }
    const conValor = ["porcentaje", "monto_fijo", "precio_especial"].includes(tipo.value);
    const diasModo = $('input[name="dias-modo"]:checked').value;
    const diasEspecificos = diasModo === "especificos";
    const dias = diasEspecificos
      ? Array.from(diasPanel.querySelectorAll('input:checked')).map((item) => Number(item.value))
      : [0, 1, 2, 3, 4, 5, 6];
    if (diasEspecificos && (!dias.length || dias.some((dia) => !Number.isInteger(dia) || dia < 0 || dia > 6))) {
      estado.textContent = "Seleccioná al menos un día válido."; return;
    }
    const horarioModo = $('input[name="horario-modo"]:checked').value;
    const franja = horarioModo === "franja";
    const horaDesde = $("#beneficios-20-hora-desde").value;
    const horaHasta = $("#beneficios-20-hora-hasta").value;
    if (franja && (!horaDesde || !horaHasta)) {
      estado.textContent = "Completá las horas Desde y Hasta."; return;
    }
    if (franja && horaDesde === horaHasta) {
      estado.textContent = "La franja horaria no puede abarcar cero horas."; return;
    }
    const limitado = $('input[name="cupo-modo"]:checked').value === "limitado";
    const payload = {
      titulo: textoPromocion.textContent, tipo_descuento: tipo.value,
      detalle_promocion: $("#beneficios-20-detalle-promocion").value.trim() || null,
      valor_descuento: conValor ? valor.value : null,
      vigente_desde: fechaConZona(inicio.value), vigente_hasta: fechaConZona(fin.value),
      dias_modo: diasModo, dias_semana: dias, horario_modo: horarioModo,
      hora_desde: franja ? horaDesde : null, hora_hasta: franja ? horaHasta : null,
      cupo_maximo: limitado ? $("#beneficios-20-cupo").value : null,
      maximo_unidades_codigo: $("#beneficios-20-unidades").value,
      mostrar_codigos_restantes: $("#beneficios-20-mostrar-restantes").checked,
      tema_etiqueta: $('input[name="tema"]:checked').value, metodos: ["whatsapp"]
    };
    payload.objetivo_tipo = objetivo.tipo;
    payload.objetivo_id = objetivo.id;
    if (objetivo.tipo === "publicacion") payload.publicacion_id = objetivo.id;
    if (objetivo.tipo === "gastronomia_producto") payload.gastronomia_producto_id = objetivo.id;
    estado.textContent = "Guardando…";
    enviando = true;
    botonGuardar.disabled = true;
    try {
      const ruta = beneficioActual
        ? "/panel/beneficios/" + encodeURIComponent(beneficioActual.id)
        : objetivo.rutaCreacion;
      const respuesta = await fetch(ruta, {
        method: beneficioActual ? "PUT" : "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": modal.dataset.csrfToken },
        body: JSON.stringify(payload)
      });
      const datos = await respuesta.json();
      if (!respuesta.ok) throw new Error(datos.mensaje || "No se pudo guardar.");
      const item = botonActual.closest(".panel-publicacion-item, .beneficios-20-demo-publicacion");
      let badge = item && item.querySelector("[data-beneficio-etiqueta]");
      if (!badge && item) { badge = document.createElement("span"); badge.className = "beneficios-20-etiqueta"; badge.dataset.beneficioEtiqueta = ""; item.querySelector("h3, h2").after(badge); }
      if (badge) badge.textContent = datos.beneficio.etiqueta;
      estado.textContent = beneficioActual
        ? "Oferta actualizada correctamente."
        : "Beneficio guardado correctamente.";
      await cargarOfertas();
      setTimeout(cerrar, 700);
    } catch (error) {
      estado.textContent = error.message || "No se pudo guardar.";
      enviando = false;
      botonGuardar.disabled = false;
    }
  });

  function seleccionarRadio(nombre, valor) {
    const radio = modal.querySelector(`input[name="${nombre}"][value="${valor}"]`);
    if (radio) radio.checked = true;
  }

  function botonParaOferta(oferta) {
    const atributo = oferta.objetivo_tipo === "gastronomia_producto"
      ? "data-gastronomia-producto-id"
      : "data-publicacion-id";
    return document.querySelector(
      `.beneficios-20-abrir-modal[${atributo}="${CSS.escape(oferta.objetivo_id)}"]`
    );
  }

  function editarOferta(oferta) {
    const botonObjetivo = botonParaOferta(oferta);
    if (!botonObjetivo || !oferta.activo) return;
    formulario.reset();
    beneficioActual = oferta;
    tipo.value = oferta.tipo_descuento;
    valor.value = oferta.valor_descuento === null ? "" : oferta.valor_descuento;
    $("#beneficios-20-detalle-promocion").value = oferta.detalle_promocion || "";
    inicio.value = visibleDesdeIso(oferta.vigente_desde);
    fin.value = visibleDesdeIso(oferta.vigente_hasta);
    seleccionarRadio("inicio", "programado");
    const todos = oferta.dias_semana.length === 7;
    seleccionarRadio("dias-modo", todos ? "todos" : "especificos");
    diasPanel.hidden = todos;
    diasPanel.querySelectorAll("input").forEach((item) => {
      item.checked = oferta.dias_semana.includes(Number(item.value));
    });
    sincronizarDias();
    const franja = Boolean(oferta.hora_desde && oferta.hora_hasta);
    seleccionarRadio("horario-modo", franja ? "franja" : "todo");
    horarioPanel.hidden = !franja;
    $("#beneficios-20-hora-desde").value = oferta.hora_desde || "";
    $("#beneficios-20-hora-hasta").value = oferta.hora_hasta || "";
    sincronizarHorario();
    const limitado = oferta.cupo_maximo !== null;
    seleccionarRadio("cupo-modo", limitado ? "limitado" : "sin-limite");
    cupoWrap.hidden = !limitado;
    $("#beneficios-20-cupo").value = oferta.cupo_maximo || 50;
    $("#beneficios-20-unidades").value = oferta.maximo_unidades_codigo;
    $("#beneficios-20-mostrar-restantes").checked = oferta.mostrar_codigos_restantes;
    seleccionarRadio("tema", oferta.tema_etiqueta);
    $("#beneficios-20-modal-titulo").textContent = "Editar beneficio";
    botonGuardar.textContent = "Guardar nueva edición";
    actualizarPromocion();
    actualizarVigencia(false);
    abrir(botonObjetivo, true);
  }

  async function desactivarOferta(oferta) {
    if (!oferta.activo || !window.confirm("¿Desactivar esta oferta?")) return;
    estadoGestion.textContent = "Desactivando…";
    try {
      const respuesta = await fetch(
        "/panel/beneficios/" + encodeURIComponent(oferta.id) + "/desactivar",
        {
          method: "POST", credentials: "same-origin",
          headers: { "X-CSRF-Token": gestion.dataset.csrfToken }
        }
      );
      const datos = await respuesta.json();
      if (!respuesta.ok) throw new Error(datos.mensaje || "No se pudo desactivar.");
      estadoGestion.textContent = datos.mensaje;
      await cargarOfertas();
    } catch (error) {
      estadoGestion.textContent = error.message || "No se pudo desactivar.";
    }
  }

  function mostrarOfertas(ofertas) {
    listaOfertas.replaceChildren();
    ofertas = ofertas.filter((oferta) => Boolean(botonParaOferta(oferta)));
    if (!ofertas.length) {
      listaOfertas.textContent = "Todavía no hay ofertas configuradas.";
      return;
    }
    ofertas.forEach((oferta) => {
      const item = document.createElement("article");
      const tituloOferta = document.createElement("strong");
      tituloOferta.textContent = oferta.etiqueta + " — " + (oferta.activo ? "Activa" : "Desactivada");
      const detalle = document.createElement("p");
      const nombreObjetivo = oferta.objetivo_tipo === "gastronomia_producto"
        ? "Producto gastronómico"
        : "Publicación";
      detalle.textContent = `${nombreObjetivo} ${oferta.objetivo_id} · Edición ${oferta.numero_edicion}`;
      item.append(tituloOferta, detalle);
      if (oferta.activo) {
        const editar = document.createElement("button");
        editar.type = "button";
        editar.textContent = "Editar";
        editar.addEventListener("click", () => editarOferta(oferta));
        const desactivar = document.createElement("button");
        desactivar.type = "button";
        desactivar.textContent = "Desactivar";
        desactivar.addEventListener("click", () => desactivarOferta(oferta));
        item.append(editar, desactivar);
      }
      listaOfertas.append(item);
    });
  }

  async function cargarOfertas() {
    if (!gestion) return;
    try {
      const respuesta = await fetch("/panel/beneficios", { credentials: "same-origin" });
      const datos = await respuesta.json();
      if (!respuesta.ok) throw new Error(datos.mensaje || "No se pudieron cargar las ofertas.");
      mostrarOfertas(datos.beneficios);
    } catch (error) {
      listaOfertas.textContent = error.message || "No se pudieron cargar las ofertas.";
    }
  }
  document.addEventListener("keydown", (evento) => { if (evento.key === "Escape" && !modal.hidden) cerrar(); });
  inicio.value = visible(new Date()); actualizarPromocion(); actualizarVigencia(true); sincronizarDias(); sincronizarHorario();
  cargarOfertas();
})();

(() => {
  "use strict";
  const canje = document.getElementById("beneficios-20-canje");
  if (!canje) return;
  const formulario = document.getElementById("beneficios-20-canje-formulario");
  const codigo = document.getElementById("beneficios-20-codigo");
  const estado = document.getElementById("beneficios-20-canje-estado");
  const resultado = document.getElementById("beneficios-20-canje-resultado");
  const titulo = document.getElementById("beneficios-20-canje-titulo");
  const vencimiento = document.getElementById("beneficios-20-canje-vencimiento");
  const consumir = document.getElementById("beneficios-20-consumir");
  let codigoValidado = null;

  function reiniciarResultado() {
    codigoValidado = null;
    resultado.hidden = true;
    consumir.disabled = true;
  }

  async function solicitar(ruta, codigoCupon) {
    const respuesta = await fetch(ruta, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": canje.dataset.csrfToken
      },
      body: JSON.stringify({ codigo: codigoCupon })
    });
    const datos = await respuesta.json();
    if (!respuesta.ok) throw new Error(datos.mensaje || "No se pudo validar el código.");
    return datos;
  }

  codigo.addEventListener("input", () => {
    codigo.value = codigo.value.toUpperCase().replace(/\s+/g, "");
    reiniciarResultado();
    estado.textContent = "";
  });

  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    reiniciarResultado();
    const valor = codigo.value.trim().toUpperCase();
    if (!valor) {
      estado.textContent = "Ingresá un código.";
      return;
    }
    estado.textContent = "Validando…";
    try {
      const datos = await solicitar("/panel/beneficios/codigos/validar", valor);
      codigoValidado = valor;
      titulo.textContent = datos.cupon.titulo;
      vencimiento.textContent = "Vence: " + new Intl.DateTimeFormat("es-AR", {
        dateStyle: "medium", timeStyle: "short"
      }).format(new Date(datos.cupon.vence_en));
      resultado.hidden = false;
      consumir.disabled = false;
      estado.textContent = datos.mensaje;
    } catch (error) {
      estado.textContent = error.message || "No se pudo validar el código.";
    }
  });

  consumir.addEventListener("click", async () => {
    if (!codigoValidado) return;
    consumir.disabled = true;
    estado.textContent = "Confirmando uso…";
    try {
      const datos = await solicitar(
        "/panel/beneficios/codigos/consumir",
        codigoValidado
      );
      estado.textContent = datos.mensaje;
      vencimiento.textContent = "Estado: utilizado.";
      codigo.disabled = true;
    } catch (error) {
      estado.textContent = error.message || "No se pudo marcar el cupón.";
    }
  });
})();
