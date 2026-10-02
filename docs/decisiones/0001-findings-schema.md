# ADR 0001 — Contrato común de findings (`findings-schema`)

- **Estado:** aceptado
- **Fecha:** 2026-09-30
- **Alcance:** `packages/findings-schema` y todos los paquetes que producen o consumen findings.

## Contexto

`venom-doctor`, `pr-agent` y `arch-committee` intercambian hallazgos (*findings*). Sin un contrato único cada
paquete inventaría su formato, y el flujo `venom-doctor → pr-agent → arch-committee` se rompería en las
costuras. Hay código en Go (`venom-doctor`) y en Python (`pr-agent`, `arch-committee`), por lo que el contrato
debe ser independiente del lenguaje.

## Decisiones

1. **JSON Schema (draft 2020-12) es la única fuente de verdad.** El archivo
   `schema/finding.schema.json` define el formato; los tipos Go y Python son una vista tipada del mismo y
   ambos validan contra ese mismo archivo (Go lo embebe con `go:embed`, Python lo lee desde el repo).
2. **Dos implementaciones equivalentes, verificadas por paridad.** Structs Go con tags `json` y modelos
   pydantic v2. Un test de paridad ejecuta el CLI `findings-validate` (Go) y el validador Python sobre todos
   los ejemplos y exige el mismo veredicto y las mismas violaciones `(ruta JSON Pointer, keyword)`.
3. **Sin `format`, solo `pattern`.** `format` (`date-time`, `uri`) es opcional en JSON Schema y cada
   biblioteca lo valida distinto, lo que rompería la paridad. `detected_at` y `references` usan expresiones
   regulares simples, compatibles con ambos motores.
4. **Contrato cerrado (`additionalProperties: false`).** Un campo desconocido o mal escrito es un error, no
   algo que se ignora en silencio. El costo es que agregar campos exige actualizar el schema (ver
   versionado).
5. **Campos obligatorios mínimos pero con evidencia.** Son obligatorios `id`, `schema_version`, `source`,
   `severity`, `title`, `resource`, `evidence` (al menos una), `root_cause`, `suggested_fix` (con al menos un
   paso), `risk_of_fix` y `detected_at`. `references` y `tags` son opcionales.
6. **Identificador de regla estable:** `id` con forma `PREFIJO-AREA-NNN` (p. ej. `KD-K8S-001`); el schema
   exige mayúsculas, dígitos y guiones.
7. **Mensajes de error claros:** ambos validadores devuelven todas las violaciones con la ruta al campo
   (`/severity`), el keyword que falló y un mensaje legible; el JSON malformado se reporta como una
   violación más y no como una excepción.

## Política de versionado

El schema usa **versionado semántico** y el campo `schema_version` de cada finding indica con qué versión se
produjo. Este schema cubre la serie `1.x` (`schema_version` debe coincidir con `^1\.\d+\.\d+$`).

| Cambio | Versión | Ejemplo |
|---|---|---|
| Incompatible: quitar o renombrar un campo, volverlo obligatorio, estrechar valores de un enum | **MAJOR** | Se publica un schema nuevo (`2.x`) y el `$id` incluye la versión; los consumidores migran explícitamente. |
| Compatible hacia atrás: campo **opcional** nuevo, valor nuevo de enum en un campo de salida | **MINOR** | Agregar `confidence` opcional. |
| Aclaraciones, descripciones, ejemplos, correcciones sin cambio de comportamiento | **PATCH** | Mejorar un `description`. |

Reglas operativas:

- Todo cambio al schema requiere un ADR (o una actualización de este) y nuevos ejemplos válidos/inválidos.
- Como el contrato está cerrado (decisión 4), **primero se actualiza el schema y los validadores y después los
  productores** que emitan el campo nuevo.
- Los productores escriben en `schema_version` la versión exacta contra la que validaron.
- Un consumidor acepta cualquier `1.x` que conozca; ante una MAJOR desconocida debe rechazar el documento.

## Dependencias

- Go: `github.com/santhosh-tekuri/jsonschema/v6` (soporte completo de 2020-12 y mantenimiento activo) y
  `golang.org/x/text` (mensajes de error localizados por esa biblioteca).
- Python: `pydantic>=2` (modelos tipados) y `jsonschema>=4.18` (`Draft202012Validator`).

Son las opciones estándar de cada ecosistema; no se escribió un validador propio.

## Consecuencias

- Hay una sola definición del formato y cualquier desviación entre Go y Python rompe el CI.
- Agregar un campo cuesta más que en un contrato abierto, a cambio de detectar errores de escritura.
- El paquete Python lee el schema desde el árbol del repo; al publicarlo como wheel habrá que vendorizar el
  archivo en el build (pendiente hasta que se distribuya fuera del monorepo).
- La gestión de dependencias de Python (`uv` vs. `poetry`) sigue pendiente (sección 11 de `CLAUDE.md`):
  `pyproject.toml` usa metadatos estándar PEP 621 y funciona con ambas herramientas.
