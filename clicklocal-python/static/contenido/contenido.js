document.documentElement.classList.add("contenido-js-activo");

const editor = document.querySelector("[data-contenido-editor]");

if (editor) {
  const preview = editor.querySelector("[data-contenido-preview]");
  const previewImagen = editor.querySelector("[data-preview-imagen]");
  const formatoNombre = editor.querySelector("[data-formato-nombre]");
  const opcionesFoto = [...editor.querySelectorAll("[data-contenido-imagen]")];
  const opcionesFormato = [...editor.querySelectorAll('input[name="formato"]')];
  const nombresFormato = {
    post: "Post · 1080 × 1080",
    historia: "Historia · 1080 × 1920",
    estado: "Estado de WhatsApp · 1080 × 1920",
  };

  opcionesFoto.forEach((opcion) => {
    opcion.addEventListener("click", () => {
      opcionesFoto.forEach((item) => {
        const activa = item === opcion;
        item.classList.toggle("is-active", activa);
        item.setAttribute("aria-pressed", activa ? "true" : "false");
      });
      if (previewImagen) {
        previewImagen.src = opcion.dataset.contenidoImagen;
      }
    });
  });

  opcionesFormato.forEach((opcion) => {
    opcion.addEventListener("change", () => {
      if (!opcion.checked || !preview) return;
      preview.classList.remove(
        "contenido-preview--post",
        "contenido-preview--historia",
        "contenido-preview--estado",
      );
      preview.classList.add(`contenido-preview--${opcion.value}`);
      if (formatoNombre) {
        formatoNombre.textContent = nombresFormato[opcion.value] || opcion.value;
      }
    });
  });

  const enfocar = (selector) => {
    const destino = editor.querySelector(selector);
    if (destino) destino.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  editor.querySelector("[data-cambiar-foto]")?.addEventListener("click", () => {
    enfocar("#seleccion-foto");
  });
  editor.querySelector("[data-cambiar-formato]")?.addEventListener("click", () => {
    enfocar("#seleccion-formato");
  });

  const botonCopiar = editor.querySelector("[data-copiar-texto]");
  const texto = editor.querySelector("[data-contenido-texto]");
  const estadoCopia = editor.querySelector("[data-copia-estado]");
  botonCopiar?.addEventListener("click", async () => {
    if (!texto) return;
    try {
      await navigator.clipboard.writeText(texto.value);
      if (estadoCopia) estadoCopia.textContent = "Texto copiado";
    } catch (_error) {
      texto.select();
      document.execCommand("copy");
      if (estadoCopia) estadoCopia.textContent = "Texto copiado";
    }
  });
}
