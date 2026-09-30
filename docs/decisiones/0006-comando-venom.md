# ADR 0006 — Comando paraguas `venom`

- **Estado:** aceptado
- **Fecha:** 2026-09-30
- **Alcance:** `packages/venom`.

## Contexto

VenomOps tiene tres herramientas con tres nombres (`kubectl doctor`, `pr-agent`, `arch-committee`) y dos lenguajes
(kdoctor en Go, las demás en Python). Para el usuario es más natural un único comando con verbos: `venom doctor`,
`venom fix`, `venom review`.

## Opciones

1. **Paraguas en Python (elegida).** Un paquete `venomops` con un CLI de `typer` que depende de `pr-agent` y
   `arch-committee` y que ejecuta el binario `kubectl-doctor`.
2. **Dispatcher en Go** al estilo de `git`/`kubectl` (busca `venom-<verbo>` en el `PATH`). Da un binario único, pero
   obliga a empaquetar y distribuir aparte las herramientas Python, que serían igualmente ejecutables externos.
3. **Reescribir todo en un solo lenguaje.** Descartada: rehace código probado sin aportar al usuario.

## Decisión

- `venom review` y `venom fix` **registran** las funciones de comando de `arch-committee` y `pr-agent`: mismas opciones,
  mismas reglas de seguridad y mismos tests, sin copiar código.
- `venom doctor` pasa los argumentos tal cual a `kubectl-doctor` (ejecutable fijo, sin shell) y devuelve su código de
  salida. Si no está en el `PATH`, explica cómo instalarlo.
- `kdoctor` **sigue llamándose `kubectl-doctor`**: krew exige ese nombre y es lo que habilita `kubectl doctor`.
- Los comandos originales (`pr-agent`, `arch-committee`) se conservan; `venom` es una capa, no un reemplazo.
- Sin cambios en las reglas de la sección 2 de `CLAUDE.md`: Bedrock solo con `--explain`/`--agent`/`--model-id`
  explícitos y sin modelo por defecto ([ADR 0005](0005-modelos-bedrock.md)); el PR siempre requiere aprobación humana.

## Consecuencias

- Flujo de extremo a extremo con un solo nombre: `venom doctor -o json | venom fix --finding - --supported`.
- Un cambio de opciones en `pr-agent`/`arch-committee` se refleja solo en `venom`; los tests de `venom` lo comprueban.
- `venom` arrastra las dependencias de ambas herramientas (boto3, PyGithub, python-hcl2): es el precio de un único
  `pip install`. Quien solo necesite una puede seguir instalando su paquete.

## Distribución (addendum)

`venom` se distribuye como `.rpm` y `.deb` autocontenidos (PyInstaller + `nfpm`), construidos en Rocky Linux 9 para
soportar glibc 2.34 en adelante; ver `packaging/`. Un `pip install` desde wheel también funciona porque el schema viaja
dentro del wheel de `findings-schema` (antes se buscaba fuera del paquete y solo funcionaba en modo editable).
Un repositorio yum/apt firmado queda como decisión aparte (exige gestionar una clave de firma).
