# Planes de ClickLocal

Documento interno de referencia sobre los planes Gratis y Premium.

Última actualización: 23 de julio de 2026.

---

# 1. Principio general

ClickLocal es una plataforma de descubrimiento comercial local.

Su función principal es permitir que una persona encuentre productos, servicios y comercios de su ciudad aunque todavía no conozca el nombre del negocio.

> Instagram muestra. ClickLocal encuentra. WhatsApp concreta.

El plan Gratis permite que cualquier comercio forme parte de la comunidad y pueda ser encontrado.

El plan Premium agrega mayor capacidad, historias, métricas y una presencia comercial más completa.

## La búsqueda es sagrada

- Premium puede tener ventajas visuales y comerciales.
- Premium no debe ocultar resultados Gratis relevantes.
- Una publicación Premium que no coincide con una búsqueda no debe aparecer por encima de una publicación Gratis que sí coincide.

---

# 2. Límites actuales confirmados en el código

| Función | Gratis | Premium |
|---|---:|---:|
| Publicaciones activas | 30 | 100 |
| Listas buscables activas | 50 | 300 |
| Fotos por publicación | Hasta 6 | Hasta 6 |
| Historias activas | 0 | 2 |
| Duración de historias | No disponible | 24 horas |
| Métricas de publicaciones | No | Sí |
| Métricas de historias | No | Sí |

Funciones del código que determinan los límites:

```python
def limite_publicaciones_por_plan(comercio):
    plan = str((comercio or {}).get("plan") or "gratis").strip().lower()
    return 100 if plan == "premium" else 30


def limite_listas_por_plan(comercio):
    plan = str((comercio or {}).get("plan") or "gratis").strip().lower()
    return 300 if plan == "premium" else 50
```

## Cambio de plan

Cuando un comercio deja de ser Premium:

- el contenido excedente no se elimina;
- las publicaciones excedentes pueden quedar pausadas;
- las listas excedentes pueden quedar pausadas;
- se utiliza `pausada_por_limite_plan`;
- las imágenes y los datos permanecen guardados.

Cuando vuelve a Premium:

- el contenido pausado por límite puede reactivarse;
- no necesita volver a cargarlo.

---

# 3. Plan Gratis actual

## Incluye

- Perfil público del comercio.
- Nombre comercial.
- Logo.
- Descripción.
- Categoría principal.
- Hasta dos categorías secundarias.
- Dirección visible.
- WhatsApp.
- Aparición en búsquedas relevantes.
- Aparición en la galería pública.
- Hasta 30 publicaciones activas.
- Hasta 6 fotos por publicación.
- Selección de foto principal.
- Edición, pausa y eliminación de publicaciones.
- Hasta 50 listas buscables activas.
- Acceso desde publicaciones hacia WhatsApp.
- Perfil con sus publicaciones activas.
- Posibilidad de solicitar Premium.

## No incluye actualmente

- Historias.
- Métricas detalladas.
- Perfil visual tipo tienda.
- Foto grande de portada.
- Banner comercial.
- Plantillas visuales.
- Buscador interno.
- Categorías internas de tienda.
- Carrito por WhatsApp.
- Videos.
- Estado “Abierto ahora”.

---

# 4. Plan Premium actual

Premium incluye todo lo disponible en Gratis y agrega:

## Capacidad

- Hasta 100 publicaciones activas.
- Hasta 6 fotos por publicación.
- Hasta 300 listas buscables activas.
- Dos historias activas simultáneas.
- Historias de 24 horas.

## Historias

Actualmente permiten:

- subir una imagen;
- escribir hasta 180 caracteres;
- vincular una publicación;
- pausar;
- reactivar mientras siga vigente;
- editar;
- eliminar;
- dirigir al perfil del comercio;
- dirigir a una publicación asociada.

## Métricas por publicación

- Visitas a la publicación.
- Clics en WhatsApp.
- Porcentaje de conversión cuando hay datos suficientes.
- Fecha desde la cual se contabilizan.

## Métricas de historias

- Visualizaciones.
- Visitas al comercio.
- Visitas a la publicación vinculada.

## Administración del plan

- Solicitud de Premium desde el panel.
- Activación desde administración.
- Duraciones administrativas de 1, 3 o 6 meses.
- Fecha de inicio.
- Fecha de vencimiento.
- Días restantes.
- Restauración del contenido pausado al regresar a Premium.

---

# 5. Página web del comercio

Función aprobada conceptualmente. Todavía no implementada.

## Reglas

- La URL se cargará desde “Mis datos”.
- No se mostrará la dirección larga.
- Se mostrará un botón como “Ir a la página web”, “Visitar tienda online” o “Ver catálogo completo”.
- No aparecerá en las tarjetas de la galería.
- No aparecerá directamente en los resultados de búsqueda.
- Se mostrará dentro del perfil público.
- WhatsApp y las publicaciones de ClickLocal conservarán prioridad.
- Abrirá en una pestaña nueva.
- Se registrará un evento como `click_sitio_web`.

## Gratis

- Podrá cargar su página web.
- Tendrá un botón secundario.
- No tendrá métricas detalladas.
- Se estudiará exigir una cantidad mínima de publicaciones activas.

## Premium

- Botón con mayor presencia visual.
- Cantidad de clics enviados a la web.
- Comparación entre visitas al perfil, WhatsApp y sitio web.
- Futuro enlace directo desde una publicación al producto exacto.

> ClickLocal descubre. La web amplía. WhatsApp concreta.

---

# 6. Tienda dentro del perfil Premium

Próximo gran desarrollo aprobado conceptualmente.

No se creará otra plataforma ni otro sitio separado. El perfil Premium evolucionará para parecer una tienda online dentro de ClickLocal.

## Propuesta comercial

> Una tienda dentro de ClickLocal, con tráfico local y pedidos por WhatsApp.

## Perfil Gratis

Conservará el formato básico:

- logo;
- nombre;
- descripción;
- dirección;
- WhatsApp;
- publicaciones;
- volver a la galería.

## Perfil Premium tipo tienda

Podrá incluir:

- foto grande de portada;
- logo;
- nombre comercial destacado;
- descripción;
- banner de oferta o novedad;
- catálogo organizado;
- hasta 100 publicaciones activas;
- buscador interno;
- categorías internas;
- plantillas visuales;
- carrito simple;
- pedido enviado por WhatsApp;
- métricas;
- historias;
- videos cuando se implementen;
- navegación permanente hacia ClickLocal.

## Identidad

Ejemplo:

> Boutique Juanita · ClickLocal Paraná

Futura dirección amigable posible:

```text
clicklocal.com.ar/parana/boutique-juanita
```

Siempre debe existir:

> Volver a la galería

---

# 7. Plantillas visuales Premium

## Minimalista clara

- fondo claro;
- diseño limpio;
- fotografía protagonista;
- pocos elementos decorativos.

## Cálida

- tonos naturales y suaves;
- sensación cercana o artesanal;
- presentación amable.

## Moderna oscura

- fondos oscuros;
- contraste fuerte;
- apariencia contemporánea.

Las plantillas solo deben cambiar la presentación. No deben existir tres sistemas técnicos distintos.

---

# 8. Catálogo Premium

Las publicaciones actuales se reutilizarán como productos.

No se creará inicialmente otra tabla paralela de productos.

Cada publicación ya tiene nombre, precio, descripción, hasta 6 imágenes, foto principal, estado activo, detalle, comercio asociado, métricas y acceso a WhatsApp.

El límite Premium de 100 publicaciones permite tener hasta 100 productos activos.

---

# 9. Listas buscables y categorías internas

No son lo mismo y no deben mezclarse.

## Listas buscables

Sirven para ayudar al buscador general mediante palabras relacionadas, variantes, atributos y formas alternativas de búsqueda.

No se muestran en el perfil público.

## Categorías internas de tienda

Servirán para organizar visualmente las publicaciones.

Ejemplos:

- Remeras.
- Pantalones.
- Accesorios.
- Tazas.
- Mates.
- Ofertas.
- Hamburguesas.
- Papas.
- Bebidas.

Las listas buscables existentes no deben modificarse para construir la tienda.

---

# 10. Buscador interno Premium

El buscador interno revisará solamente las publicaciones del comercio cuyo perfil se está visitando.

No reemplaza el buscador general de ClickLocal.

---

# 11. Carrito por WhatsApp

El carrito servirá para seleccionar productos y preparar una consulta.

ClickLocal no procesará pagos.

## Recorrido

1. El cliente entra al perfil Premium.
2. Agrega productos.
3. Modifica cantidades.
4. Ve un total estimado.
5. Envía el pedido por WhatsApp.

## El comercio confirma

- disponibilidad;
- precio vigente;
- variantes;
- medio de pago;
- entrega;
- retiro;
- envío.

## ClickLocal no hará inicialmente

- cobros;
- procesamiento de tarjetas;
- Mercado Pago integrado;
- control de stock;
- cálculo de envíos;
- facturación;
- devoluciones;
- resolución de reclamos.

> ClickLocal arma y comunica el pedido. El comercio concreta la operación.

---

# 12. Historias Premium futuras

Estado actual:

- dos historias activas;
- duración de 24 horas.

Cambio aprobado para después:

- aumentar de dos a cuatro historias activas;
- mantener inicialmente las 24 horas;
- revisar antes el impacto en el visor, el panel y la portada.

---

# 13. Videos Premium

Idea aprobada para evaluar. Todavía no implementada.

Propuesta inicial:

- solamente Premium;
- carrusel propio;
- un video activo por comercio;
- máximo aproximado de 20 o 30 segundos;
- sin reproducción automática en la galería;
- miniatura o portada;
- enlace al comercio, publicación o WhatsApp;
- duración posible de 24 horas, 3 días o 7 días;
- duración recomendada inicial: 3 días.

Al vencer debe dejar de mostrarse y un proceso posterior debe eliminar físicamente el archivo de Storage.

---

# 14. Abierto ahora

Función prevista para Premium.

Podría incluir horarios comerciales, indicador “Abierto ahora”, presencia en el perfil, posible filtro de búsqueda y administración sencilla desde el panel.

---

# 15. Resumen de funciones Premium

| Función | Estado |
|---|---|
| 100 publicaciones activas | Disponible |
| 300 listas buscables | Disponible |
| Hasta 6 fotos por publicación | Disponible |
| Dos historias activas | Disponible |
| Historias de 24 horas | Disponible |
| Métricas de publicaciones | Disponible |
| Métricas de historias | Disponible |
| Inicio y vencimiento del plan | Disponible |
| Página web del comercio | Próximo desarrollo |
| Métricas de clics hacia la web | Próximo desarrollo |
| Perfil tipo tienda | Próximo gran desarrollo |
| Foto grande de portada | Aprobado, no implementado |
| Banner comercial | Aprobado, no implementado |
| Tres plantillas visuales | Aprobado, no implementado |
| Buscador interno | Aprobado, no implementado |
| Categorías internas | Aprobado, no implementado |
| Carrito por WhatsApp | Aprobado, no implementado |
| Cuatro historias activas | Aprobado para después |
| Videos temporales | En evaluación |
| Abierto ahora | Previsto |
| Pagos dentro de ClickLocal | No previsto actualmente |
| Stock y envíos | No previstos actualmente |

---

# 16. Orden recomendado de desarrollo

## Etapa 1

- Documentar los planes.
- Incorporar página web del comercio.
- Registrar clics externos.
- Mantener las publicaciones como contenido principal.
- Vender Premium con las funciones existentes.

## Etapa 2

- Crear una demo separada del perfil Premium tipo tienda.
- Probar las tres plantillas.
- Diseñar portada y banner.
- Definir categorías internas.
- Diseñar buscador interno.
- Diseñar carrito por WhatsApp.

## Etapa 3

- Implementación pequeña y controlada.
- Prueba con dos o tres comercios Premium.
- Medición de visitas, productos agregados, pedidos enviados y clics a WhatsApp.

## Etapa 4

- Mejoras basadas en uso real.
- Cuatro historias.
- Videos temporales.
- Abierto ahora.

---

# 17. Reglas de implementación

Antes de cualquier modificación:

1. Inspección en modo solo lectura.
2. Explicación exacta del cambio.
3. Identificación de archivos y tablas afectados.
4. Explicación de riesgos.
5. Autorización explícita.
6. Backup.
7. Cambio pequeño y controlado.
8. Validación.
9. No incluir archivos ajenos.
10. No hacer commit, push ni publicación sin aprobación.

---

# 18. Decisiones pendientes

- Cantidad mínima de publicaciones para habilitar la página web.
- Diseño exacto del botón web Gratis.
- Diseño exacto del botón web Premium.
- Cantidad máxima de categorías internas.
- Forma de asignar una publicación a una categoría.
- Cantidad de banners.
- Dimensiones de la portada.
- Personalización de colores.
- Productos sin precio.
- Carrito con productos que dicen “Consultar”.
- Total estimado del carrito.
- Métricas exactas del carrito.
- Qué ocurre visualmente cuando vence Premium.
- Qué configuración Premium se conserva.
- Política de almacenamiento para portadas, banners y videos.

---

# 19. Mensajes comerciales provisionales

## Gratis

> Tu comercio forma parte de ClickLocal y puede aparecer cuando una persona de la ciudad busca lo que vendés.

## Premium actual

> Más capacidad, historias y métricas para conocer cómo encuentran y contactan tu comercio.

## Premium con tienda

> Tu propia tienda dentro de ClickLocal, con catálogo, diseño personalizado, carrito por WhatsApp y tráfico local.

---

# 20. Fuente de verdad

Los límites técnicos vigentes siempre deben verificarse en el código.

Este documento explica el producto y debe actualizarse cada vez que cambie una regla, un límite o una función.

Debe distinguirse siempre entre:

- disponible actualmente;
- aprobado, pero no implementado;
- idea todavía en evaluación.
