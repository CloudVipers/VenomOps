# ADR 0007 — Gestión de dependencias Python: `pyproject.toml` estándar, `pip` por defecto y `uv` opcional

- **Estado:** aceptado
- **Fecha:** 2026-10-01
- **Alcance:** `findings-schema/python`, `pr-agent`, `arch-committee` y `venom`. Resuelve la decisión abierta de la
  sección 11 de `CLAUDE.md` (`uv` vs. `poetry`).

## Contexto

Hay cuatro paquetes Python que dependen entre sí (`venom` → `pr-agent`/`arch-committee` → `findings-schema`) y que
se instalan en modo editable desde el monorepo. Había que decidir con qué herramienta se gestionan las dependencias.

## Decisión

1. **El formato es `pyproject.toml` estándar (PEP 621) con `hatchling` como backend de build.** No hay formato propio de
   ninguna herramienta, así que cualquier instalador sirve.
2. **`pip` + `venv` es el camino por defecto** (y el del CI): no exige instalar nada extra.
3. **`uv` es un acelerador opcional.** Los `Makefile` lo usan si está instalado (`uv venv` + `uv pip install`) y, si no,
   caen a `python -m venv` + `pip`. Mismo `pyproject.toml`, mismo resultado.
4. **No se adopta `poetry`.** Aporta un formato de metadatos propio y un lock por paquete que, en un monorepo con
   dependencias locales editables entre paquetes, añade fricción sin dar beneficio claro.
5. **Dependencias con rangos compatibles** (`>=` con tope mayor donde hay riesgo, p. ej. `pydantic>=2.6,<3`) y justificación en
   el PR al añadir una nueva (regla de la sección 3 de `CLAUDE.md`).

## Consecuencias

- Instalar cualquier paquete no requiere herramientas especiales; quien use `uv` obtiene instalaciones más rápidas.
- **No hay *lockfile*:** dos instalaciones en fechas distintas pueden resolver versiones distintas de las dependencias
  transitivas. Para los paquetes `.rpm`/`.deb` de `venom` (que congelan las dependencias con PyInstaller) esto significa
  que el binario depende del día de la construcción. Si se necesita reproducibilidad exacta, el siguiente paso sería un
  archivo de *constraints* generado con `uv pip compile` para el build de release, sin cambiar de herramienta.
