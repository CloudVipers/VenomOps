# ADR 0009 — Valor `venom-doctor` en `source` del contrato (schema 1.1.0)

- **Estado:** aceptado
- **Fecha:** 2026-10-02
- **Alcance:** `packages/findings-schema` (Go y Python). Primer paso del renombrado de `kdoctor` a `venom-doctor`.

## Contexto

La herramienta de diagnóstico se llamaba `kdoctor` y pasa a llamarse `venom-doctor`, para que todo el producto sea `venom`. El
contrato de findings (ADR 0001) tiene un campo `source` con un enum cerrado que incluye `kdoctor`, y `CLAUDE.md` exige un ADR
para cambiarlo.

## Opciones

1. **Renombrar el valor** (`kdoctor` pasa a `venom-doctor`). Estrecha el enum: según la política de versionado es un cambio
   **MAJOR** (`2.x`) y rompe la lectura de todos los findings ya generados (los ejemplos, los archivos guardados, la salida de
   versiones anteriores de la herramienta).
2. **Añadir `venom-doctor` y conservar `kdoctor`** (elegida). Un valor nuevo en un enum de salida es **MINOR** (`1.1.0`):
   compatible hacia atrás y hacia delante dentro de la serie 1.x.

## Decisión

- El enum de `source` pasa a `venom-doctor | kdoctor | pr-agent | arch-committee | manual`.
- **`venom-doctor` es el valor que deben emitir los productores.** `kdoctor` queda **obsoleto**: sigue siendo válido para
  poder leer findings anteriores, pero ningún productor lo emite.
- El schema, el `$id` y los paquetes pasan a `1.1.0`. En Go se añade `SourceVenomDoctor` (y `SourceKDoctor` queda marcado
  como `Deprecated`); en Python, `Source.VENOM_DOCTOR`.
- Se añaden ejemplos válidos con `venom-doctor` y uno inválido con una fuente desconocida; los ejemplos con `kdoctor` se
  conservan como prueba de compatibilidad.
- Orden de despliegue (política del ADR 0001): **primero schema y validadores, después los productores**. El renombrado de la
  herramienta pasa a emitir `venom-doctor` solo cuando este cambio ya está en `main`.

## Consecuencias

- Los consumidores de la serie 1.x que ya conocían `1.0.0` rechazarían un finding con `venom-doctor` si validan con el schema
  viejo; por eso se actualiza el schema antes que el productor. Los consumidores nuevos leen ambos valores.
- `kdoctor` no se podrá eliminar hasta una versión MAJOR del schema; mientras tanto no cuesta nada mantenerlo.
