# ADR 0011 — VenomOps como prototipo de una línea de investigación

- **Estado:** propuesto
- **Fecha:** 2026-11-23
- **Alcance:** repositorio completo. Agrega la Fase 6 a `CLAUDE.md`.

## Contexto

VenomOps se usará como prototipo y caso de estudio de un trabajo de grado de maestría en ciberseguridad sobre la
seguridad de agentes LLM que revisan y modifican infraestructura como código. Una tesis necesita una pregunta de
investigación, un modelo de amenazas formal y una evaluación cuantitativa reproducible, que hoy el repositorio no tiene.

## Decisión

1. La documentación de investigación vive en `docs/investigacion/` (en español, como el resto de `docs/`).
2. El benchmark y el corpus de inyección viven en `examples/benchmark/`, con datos **sintéticos** y licencia Apache 2.0.
3. El arnés de evaluación vive en `research/eval/`, **fuera** de los paquetes del producto: no se publica en los paquetes
   `.rpm`/`.deb` ni amplía las dependencias de `pr-agent` o `arch-committee`.
4. Los experimentos no relajan ninguna regla de la sección 2 de `CLAUDE.md`. Los ataques que buscan ejecutar comandos
   se registran en ejecutores simulados; nunca se ejecutan.
5. Checkov y Trivy se usan como **línea base externa**: se ejecutan aparte y el arnés solo lee su salida JSON; no son
   dependencias del repositorio.
6. Las mejoras a los controles que salgan de los experimentos siguen el flujo normal: ADR, rama propia y PR pequeño.

## Consecuencias

- La investigación produce artefactos útiles para el producto (benchmark de regresión, pruebas de inyección).
- Los resultados con LLM tienen costo; se ejecutan a mano, no en CI. CI solo corre las pruebas deterministas del arnés.
- El trabajo de grado debe citar el repositorio y su licencia; la propiedad intelectual se acuerda con la universidad.
