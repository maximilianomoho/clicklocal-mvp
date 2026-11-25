# Dependencias de Beneficios 1A

## Convenciones

**Verificado** describe referencias observadas en el código de la copia aislada.
**Propuesto** describe contratos o integraciones aún no conectados.

## Dependencias directas

| Dependencia | Estado | Sentido | Uso y límite |
|---|---|---|---|
| Python estándar (`dataclasses`, `datetime`, `decimal`, `enum`, `typing`) | Verificado | Beneficios → Python | Tipos, fechas, dinero y protocolo de repositorio. |
| Flask | Verificado como dependencia existente; integración propuesta | `routes.py` → Flask | Construcción diferida del blueprint y respuestas HTTP. El núcleo no depende de Flask. |
| `publicaciones.id` | Tabla usada por código existente; relación propuesta | Beneficios → Publicaciones | Identificador de objetivo `publicacion`. No se consulta directamente en 1A. |
| `gastronomia_productos.id` | Tabla y SQL local verificados; relación propuesta | Beneficios → Gastronomía | Identificador de objetivo `gastronomia_producto`. No se consulta directamente en 1A. |
| Repositorio real de Beneficios | Propuesto y pendiente | Servicios → puerto de persistencia | Debe implementar `RepositorioBeneficios`; no existe implementación Supabase en 1A. |

No se verificaron tablas ni restricciones en Supabase: no hubo conexión remota.

## Archivos y funciones externos necesarios para integrar

- **Propuesto:** un punto de composición autorizado deberá construir el
  repositorio real, el servicio y registrar `crear_blueprint_beneficios`.
- **Propuesto:** las pantallas consumidoras llamarán contratos de
  `beneficios.contracts`; no importarán `repository.py` ni estructuras internas.
- **Pendiente:** SQL y rollback propios antes de implementar persistencia.

No se modifica ningún archivo externo en este lote.

## Contratos públicos ofrecidos

- `consultar_beneficios_vigentes`
- `consultar_para_publicaciones`
- `consultar_para_gastronomia_productos`
- `obtener_detalle_beneficio`
- `generar_codigo_beneficio`
- `crear_blueprint_beneficios` (fábrica diferida, todavía no registrada)

Los contratos exigen un `ServicioBeneficios` inyectado. No usan globales ni
crean clientes de base de datos. `SolicitudCodigo` no acepta un instante del
consumidor: el servicio obtiene la fecha de emisión de su reloj zonificado y la
pasa explícitamente al repositorio.

## Dependencias inversas

Consumidores propuestos, todavía no conectados:

| Consumidor | Contrato permitido | Comportamiento ante fallo |
|---|---|---|
| Galerías de publicaciones | `consultar_para_publicaciones` | Continuar sin mostrar beneficios. |
| Ficha de publicación | consulta y detalle | Mantener la ficha operativa. |
| Menú/listado gastronómico | `consultar_para_gastronomia_productos` | Mantener productos y precios normales. |
| Ficha de producto gastronómico | consulta, detalle y código | Mantener la ficha; mostrar mensaje no técnico al pedir código. |
| Administración futura de Beneficios | servicios y errores públicos | No informar éxito si la persistencia falla. |

## Dependencias expresamente excluidas

- motores de carrito y cálculo de pedidos;
- POS, caja y cierres;
- modificación del precio persistido de publicaciones o productos;
- `app.py` o `gastronomia/routes.py` como lugar de lógica de Beneficios;
- imports desde otros módulos hacia `repository.py` o datos internos;
- servicios externos nuevos, llamadas de red y credenciales propias;
- dependencia directa de Supabase desde reglas, contratos o servicios.

## Riesgos de modificar componentes compartidos

- `app.py` concentra composición y rutas: un registro incorrecto puede impedir
  iniciar toda la aplicación.
- `gastronomia/routes.py` y sus plantillas participan en catálogo y operación:
  mezclar descuentos allí aumenta acoplamiento y riesgo sobre pedidos.
- las plantillas de galerías y detalles deben degradar de forma segura cuando
  Beneficios esté deshabilitado o falle.
- las relaciones con tablas existentes deben confirmarse en SQL real antes de
  crear claves foráneas; asumir tipos de ID puede bloquear migraciones.
- aplicar un precio promocional en transacciones excede 1A y podría alterar
  totales, auditoría, POS o caja.

Todo cambio futuro de estas dependencias exige actualizar este documento dentro
del mismo alcance expresamente autorizado.
