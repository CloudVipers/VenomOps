# ADR 0002 — Modelo de seguridad de `pr-agent`

- **Estado:** aceptado
- **Fecha:** 2026-09-30
- **Alcance:** `packages/pr-agent`. Complementa las reglas inviolables de la sección 2 de `CLAUDE.md`.

## Contexto

`pr-agent` edita código de infraestructura y abre PRs. Un error o un modelo que se desvíe podría ejecutar
comandos destructivos, salirse del repositorio o publicar secretos. Las reglas de `CLAUDE.md` exigen que el bloqueo
sea **en código** y no dependa del prompt.

## Decisiones

1. **Toda ejecución pasa por `SafeRunner`** (`safety.py`): sin shell, por nombre exacto de programa
   (`terraform` o `git`, sin rutas) y con formas de argumentos permitidas **una por una** (lista blanca de
   invocaciones, no lista negra de flags). Lo demás lanza `CommandNotAllowedError` antes de crear el proceso.
2. **Terraform:** `validate`, `plan` (`-input=false -no-color -lock=false -refresh=false`) e
   `init -backend=false -input=false -no-color`. `init` **no estaba** en la lista de `CLAUDE.md`, pero
   `validate` no funciona sin cargar los providers; se permite únicamente con `-backend=false`, de modo que jamás
   toca el estado remoto ni descarga módulos de estado. Se considera una desviación mínima y deliberada.
3. **Git:** solo las formas necesarias (`rev-parse`, `status --porcelain`, `diff` de lectura, `remote get-url`,
   `switch -c fix/...`, `add -- <archivos explícitos>`, `commit -m`, `push [-u] origin fix/...`,
   `branch -D fix/...` para limpiar una rama propia). Las ramas deben cumplir `fix/<slug>` y nunca una rama
   protegida; no existen `--force`, `--delete`, refspecs, `reset`, `rebase`, `merge`, `add -A` ni `--amend`.
4. **Rutas confinadas:** `resolve_in_repo` rechaza `..`, absolutas, symlinks que escapan y archivos sensibles
   (`*.tfstate`, `*.tfvars`, `.env`, llaves, `.git/`, `.terraform/`).
5. **Sandbox primero:** el arreglo se aplica en una copia temporal; el repositorio real solo se modifica al final,
   en una rama nueva, y se restaura si algo falla antes del commit. `--dry-run` nunca lo toca.
6. **Edición estructurada y verificable** en lugar de escritura libre: tres operaciones HCL y reparseo con
   `python-hcl2`. Un arreglo debe declarar los archivos que cambia; el diff debe ser mínimo (máx. 80 líneas) y no
   crear ni borrar archivos.
7. **Sin capacidad de merge:** `PullRequestClient` solo expone `default_branch` y `open_pull_request`.
8. **LLM opcional y explícito:** los fixers deterministas son el camino por defecto. El agente LLM requiere
   `--agent` y un modelo explícito; redacta el finding antes de enviarlo y solo actúa a través del `ToolBox`.

## Consecuencias

- Los límites se prueban directamente (más de 60 invocaciones prohibidas y rutas peligrosas en `tests/test_safety.py`).
- Añadir un comando nuevo exige tocar la lista blanca y sus tests, lo que lo hace visible en la revisión.
- Agregar un fixer nuevo no puede ampliar permisos: solo dispone de las herramientas del `ToolBox`.
- `init -backend=false` es la única excepción a la lista original y está documentada aquí.
