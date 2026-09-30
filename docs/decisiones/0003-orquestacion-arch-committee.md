# ADR 0003 — Orquestación de `arch-committee`: orquestador propio sobre Bedrock Converse

- **Estado:** aceptado (confirmado 2026-09-30; resuelve la decisión abierta de la sección 11 de `CLAUDE.md`)
- **Fecha:** 2026-09-30
- **Alcance:** `packages/arch-committee`.

## Contexto

`CLAUDE.md` pide decidir entre **Strands Agents** y **LangGraph** para orquestar un comité de cuatro agentes
especialistas (security, cost, reliability, operations) y un moderador. El flujo es fijo y pequeño:

1. Ronda 1: los cuatro analizan el plan **en paralelo** y devuelven findings estructurados.
2. Ronda 2 (réplica): cada uno cuestiona hallazgos de los otros.
3. Moderador: consolida, registra desacuerdos, prioriza y asigna severidad final.

Además hay requisitos duros: tope de tokens por ejecución, número máximo de rondas configurable, redacción de
secretos antes de enviar nada, salida validada contra `findings-schema` y pruebas deterministas con el LLM simulado.

## Opciones

| Opción | A favor | En contra |
|---|---|---|
| **Strands Agents** | Nativo de AWS, agentes y herramientas listos | Dependencia pesada para un flujo de 3 pasos; el control fino del presupuesto de tokens y del formato de salida queda dentro del framework; más superficie que probar y que auditar |
| **LangGraph** | Grafos con estado, útil para flujos dinámicos | Es la dependencia más grande de las tres; el grafo aquí es lineal (2 rondas + moderador), así que no aporta; cuesta simular el LLM de forma determinista |
| **Orquestador propio mínimo** (elegida) | Cero frameworks nuevos (solo `boto3`, que `pr-agent` ya usa); el presupuesto de tokens, el paralelismo (`ThreadPoolExecutor`) y el formato estructurado (*tool use* forzado de Converse) quedan explícitos y testeables; el LLM es una interfaz de una función, trivial de simular | Hay que mantener ~300 líneas de orquestación; sin trazas ni reintentos "de fábrica" |

## Decisión

Se implementa un **orquestador propio** (`orchestrator.py`) sobre la API **Converse** de Amazon Bedrock:

- Una interfaz mínima `LLM.converse_tool(system, user, tool) -> (dict, Usage)`; el modelo **siempre** responde mediante
  una herramienta de salida obligatoria (`toolChoice`), de modo que la salida es JSON estructurado y validable.
- Un `Budget` global (`max_total_tokens`, `max_rounds`) consultado antes de cada llamada; al agotarse se omiten las
  rondas restantes y el informe lo declara (el comité degrada, no falla).
- Cada especialista tiene su *system prompt* en un archivo versionado (`agents/prompts/<agente>.v1.md`); la versión
  usada se registra en el informe.

## Consecuencias

- Sin dependencias nuevas de framework; la superficie a auditar es pequeña.
- **Revisar esta decisión si** el comité pasa a tener rondas dinámicas, agentes con herramientas propias o
  ramificaciones condicionales: entonces LangGraph (o Strands) sí justifica su peso.
- Se mantiene la regla 5: **no hay modelo por defecto**; toda ejecución exige `--model-id` o
  `ARCH_COMMITTEE_BEDROCK_MODEL`. `--dry-run` muestra exactamente qué se enviaría (ya redactado) sin llamar al modelo.
- La elección de modelos de Bedrock por defecto (sección 11) sigue abierta a propósito.


## Validación con un modelo real (2026-09-30)

El orquestador se probó contra Bedrock (Claude Haiku 4.5). Confirmó la elección (el flujo fijo y la estructura forzada por
herramienta funcionan) y obligó a tres cambios de diseño: el moderador decide **por referencia** (no re-emite evidencia ni
arreglos; prompt v2), una respuesta cortada en `max_tokens` es un **error** y no un resultado parcial, y los valores
desconocidos del plan se conservan como `(known after apply)`. Detalle en el README del paquete.
