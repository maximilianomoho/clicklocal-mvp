# Beneficios ClickLocal 2.0 — núcleo 1A

## Propósito

Beneficios administra promociones vinculadas explícitamente a una publicación o
a un producto gastronómico. El núcleo valida descuentos y vigencias, ofrece
contratos internos estables y delega toda persistencia a un puerto inyectado.

## Responsabilidades

- representar beneficios, objetivos y códigos con tipos propios;
- validar porcentajes, precios especiales, activación y períodos de vigencia;
- calcular precios promocionales informativos;
- consultar beneficios vigentes y obtener detalles mediante un repositorio;
- solicitar códigos sin declarar éxito hasta que el repositorio los persista;
- traducir errores controlados a mensajes públicos no técnicos.

## Límites

El módulo no modifica precios fuente ni interviene en carrito, pedidos, POS o
caja. Tampoco autentica usuarios, registra su blueprint, crea tablas, configura
Supabase ni decide cómo se presentan promociones en pantallas existentes.

`calcular_precio_con_beneficio` es una regla pura e informativa. Que un precio
se use en una operación comercial requerirá un diseño y autorización separados.

## Funcionamiento del núcleo

1. Un consumidor crea `ObjetivoBeneficio` con tipo e identificador explícitos.
2. Los contratos construyen una consulta y llaman a `ServicioBeneficios`.
3. El servicio usa un `RepositorioBeneficios` inyectado y vuelve a validar cada
   resultado; no confía en filas externas sin validarlas.
4. La vigencia exige fechas con zona horaria y considera inclusivos ambos
   extremos del período.
5. La emisión de códigos obtiene el instante actual de un reloj del servidor
   con zona horaria, verifica que el beneficio esté vigente y entrega ese mismo
   instante al repositorio. La solicitud del consumidor no contiene fechas.

## Fallos y desactivación

La implementación incluida `RepositorioBeneficiosNoConfigurado` siempre lanza
`PersistenciaNoConfiguradaError`. Esto representa honestamente el estado de 1A:
no hay esquema ni conexión real. Los consumidores visuales deberán capturar
`BeneficiosError` y continuar sin promociones. Las rutas responden mensajes
seguros, pero aún no están registradas en la aplicación.

## Estado verificado y arquitectura propuesta

Verificado en la copia aislada del respaldo `20261115T214302-0500`:

- el código existente consulta las tablas `publicaciones` y
  `gastronomia_productos`;
- `gastronomia_productos` tiene definición SQL local en
  `sql/gastronomia_esquema_consolidado_v1.sql`;
- la aplicación registra blueprints de módulos desde `app.py`;
- Beneficios no estaba implementado ni registrado antes de este lote.

Propuesto, pero no implementado ni verificado contra una base remota:

- tablas propias de beneficios y códigos;
- claves foráneas hacia identificadores reales;
- políticas de acceso, índices y garantías de unicidad;
- integración visual con publicaciones y productos gastronómicos.

## Pruebas

Las pruebas de 1A usan repositorios falsos en memoria y no importan `app.py`, no
crean clientes externos y no necesitan red. En este lote se escriben, pero no se
ejecutan.
