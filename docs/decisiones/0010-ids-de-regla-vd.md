# ADR 0010 — IDs de las reglas de venom-doctor: `KD-K8S-*` pasa a `VD-K8S-*`

- **Estado:** aceptado
- **Fecha:** 2026-10-02
- **Alcance:** `packages/venom-doctor`, `pr-agent`, documentación y ejemplos. Continúa el renombrado de `kdoctor` a
  `venom-doctor` ([ADR 0009](0009-source-venom-doctor.md)).

## Contexto

Los identificadores de las reglas (`KD-K8S-001` … `KD-K8S-009`) llevaban el prefijo `KD` de `kdoctor`. El campo `id` del
contrato es «estable», pero el producto se llama `venom` y el proyecto es muy reciente: es el momento más barato de cambiarlos.

## Decisión

- Las reglas pasan a llamarse **`VD-K8S-001` … `VD-K8S-009`** (`VD` = venom-doctor). No cambia su significado ni su numeración.
- **El schema no cambia:** el patrón del `id` (`^[A-Z][A-Z0-9]+(-[A-Z0-9]+)+$`) acepta ambos prefijos, de modo que los findings
  ya guardados con `KD-K8S-*` siguen siendo válidos y los consumidores genéricos (por ejemplo `arch-committee`, que los lee como
  contexto) no necesitan cambios.
- **Compatibilidad en `pr-agent`:** el registro de fixers lee el prefijo antiguo como el actual (`KD-K8S-002` se trata como
  `VD-K8S-002`), con un test, para que un finding generado antes del renombrado se pueda seguir corrigiendo. El nombre de la rama
  del PR usa el `id` tal como llega.
- Los productores emiten solo `VD-K8S-*`. Los ejemplos legacy del contrato (`kdoctor-*.json`) conservan los IDs antiguos como
  prueba de compatibilidad.

## Consecuencias

- Quien tenga reglas, filtros o paneles que dependan del prefijo `KD-K8S-` debe actualizarlos; el alias de `pr-agent` solo cubre
  su propio registro.
- No se elimina el alias mientras puedan existir findings antiguos guardados.
