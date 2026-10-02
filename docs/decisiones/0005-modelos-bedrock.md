# ADR 0005 — Modelos de Bedrock: sin modelo por defecto en el código, uno recomendado en la documentación

- **Estado:** aceptado
- **Fecha:** 2026-09-30
- **Alcance:** `venom-doctor --explain`, `pr-agent --agent` y `arch-committee`. Resuelve la decisión abierta de la sección 11
  de `CLAUDE.md` ("modelos de Bedrock por defecto").

## Contexto

Las tres rutas con LLM son opcionales y explícitas (regla 5 de `CLAUDE.md`). Hoy cada una exige indicar el modelo
(`--explain-model`/`VENOM_DOCTOR_BEDROCK_MODEL`, `--model-id`/`PR_AGENT_BEDROCK_MODEL`, `--model-id`/`ARCH_COMMITTEE_BEDROCK_MODEL`).
Había que decidir si se fija un valor por defecto.

## Decisión

1. **El código no trae ningún modelo por defecto.** Un identificador fijo en el binario envejece (los modelos se
   retiran), depende de la región (los *inference profiles* `us.`/`eu.` cambian por región) y de qué modelos tiene
   habilitados cada cuenta. Fallar con un mensaje claro ("a model is required … there is no default") es más seguro
   que elegir en silencio el modelo de otra persona, y mantiene que la llamada sea siempre una decisión explícita.
2. **La documentación recomienda un modelo probado:** `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Claude Haiku 4.5).
   Es el que se ejecutó contra Bedrock real en las tres rutas (`--explain` sobre kind, `--agent` sobre RDS y S3, y el
   comité sobre los planes de ejemplo). Es el más barato de la familia y resultó suficiente.
3. **Quien necesite más calidad puede subir de modelo solo cambiando la variable**; el contrato es el mismo (salida
   estructurada por herramienta forzada), pero no se declara como validado ningún otro modelo hasta probarlo.
4. **Todas las salidas validan contra `findings-schema`** sea cual sea el modelo: si el modelo devuelve algo inválido
   o truncado, la ejecución falla en lugar de aceptarlo.

## Consecuencias

- Los README muestran el modelo recomendado como ejemplo, no como valor implícito.
- Cambiar de modelo recomendado es un cambio de documentación, no de código ni de versión del schema.
- Si en el futuro se quiere un valor por defecto, requerirá un ADR nuevo que trate la región y el ciclo de vida del modelo.
