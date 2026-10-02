# ADR 0004 — Convenciones de `evidence` entre paquetes (contrato accionable sin tocar el schema)

- **Estado:** aceptado
- **Fecha:** 2026-09-30
- **Alcance:** `findings-schema`, `venom-doctor`, `pr-agent`, `arch-committee`.

## Contexto

El schema de findings ([ADR 0001](0001-findings-schema.md)) es cerrado y genérico: describe *qué* pasó, no *cómo
actuar*. Para que un paquete corrija automáticamente lo que otro detectó (p. ej. `venom-doctor` → `pr-agent`), el
consumidor necesita datos **parseables** (¿de qué contenedor, de qué valor a qué valor?). Cambiar el schema por cada
caso rompería el contrato común; parsear texto libre sería frágil.

## Decisión

Los datos accionables viajan en **`evidence`** con un `kind` convenido y un `detail` con formato fijo, sin modificar el
schema:

| `kind` | Formato de `detail` | Lo produce | Lo consume |
|---|---|---|---|
| `memory-limit-change` | `container=<nombre>;from=<cantidad>;to=<cantidad>` (p. ej. `container=app;from=32Mi;to=64Mi`) | `venom-doctor` (VD-K8S-002) | `pr-agent` (fixer `VD-K8S-002`) |
| `required-tags` | `Clave=Valor;Clave2=Valor2` | cualquier fuente (p. ej. manual) | `pr-agent` (fixer `TF-TAG-001`) |
| `prior-finding` | `<id> (<fuente>): <título>` | `arch-committee` | personas (trazabilidad) |
| `debate` | `<agente> (<postura>): <argumento>` | `arch-committee` | personas |
| `committee-decision` | texto libre del moderador | `arch-committee` | personas |
| `ai-explanation` | texto libre (explicación ampliada) | `venom-doctor --explain` | personas |

Reglas:

1. Los `kind` son identificadores en minúsculas con guiones.
2. Un `detail` legible por máquinas usa pares `clave=valor` separados por `;`, con valores sin `;`.
3. **Un consumidor nunca adivina:** si falta la evidencia que necesita o está mal formada, se niega con un mensaje que
   dice qué falta (p. ej. un OOM sin límite previo no trae `memory-limit-change` y `pr-agent` no lo corrige solo).
4. Añadir un `kind` nuevo es compatible hacia atrás y se documenta aquí. **Cambiar el formato** de uno existente es un
   cambio incompatible: se crea un `kind` nuevo (por ejemplo `memory-limit-change-v2`) y se migra a los consumidores.
5. Los productores emiten **arrays** de findings (`venom-doctor -o json`); los consumidores eligen uno por `--id`,
   `--resource`, `--index` o `--supported` (el único que tiene arreglo automático).

## Mapa finding → arreglo automático (`pr-agent`)

| Finding | Arreglo |
|---|---|
| `TF-S3-001` | Agrega cifrado SSE-S3 a un bucket |
| `TF-TAG-001` | Agrega los tags faltantes |
| `TF-EBS-001` | gp2 → gp3 |
| `VD-K8S-002` | Sube `limits.memory` en el recurso `kubernetes_*` de Terraform que declara el workload |

Los demás findings (`VD-K8S-001/003/004`, `AC-*`) requieren una persona o el agente LLM opcional (`--agent`).

## Consecuencias

- El contrato entre paquetes está documentado y probado: `pr-agent` tiene pruebas con la salida **real** de `venom-doctor`
  capturada de un clúster kind (`examples/findings/venom-doctor-output.json`); se regenera con `DEMO_SAVE_FINDINGS=... run-demo.sh`.
- Un cambio en el formato de `memory-limit-change` rompe esas pruebas en CI antes de llegar a producción.
- El schema no cambia: sigue en `1.0.0`.
