# ADR 0006 — Comando paraguas `venom`

- **Estado:** aceptado
- **Fecha:** 2026-09-30
- **Alcance:** `packages/venom`.

## Contexto

VenomOps tiene tres herramientas con tres nombres (`kubectl venom-doctor`, `pr-agent`, `arch-committee`) y dos lenguajes
(kdoctor en Go, las demás en Python). Para el usuario es más natural un único comando con verbos: `venom doctor`,
`venom fix`, `venom review`.

## Opciones

1. **Paraguas en Python (elegida).** Un paquete `venomops` con un CLI de `typer` que depende de `pr-agent` y
   `arch-committee` y que ejecuta el binario `kubectl-venom_doctor`.
2. **Dispatcher en Go** al estilo de `git`/`kubectl` (busca `venom-<verbo>` en el `PATH`). Da un binario único, pero
   obliga a empaquetar y distribuir aparte las herramientas Python, que serían igualmente ejecutables externos.
3. **Reescribir todo en un solo lenguaje.** Descartada: rehace código probado sin aportar al usuario.

## Decisión

- `venom review` y `venom fix` **registran** las funciones de comando de `arch-committee` y `pr-agent`: mismas opciones,
  mismas reglas de seguridad y mismos tests, sin copiar código.
- `venom doctor` pasa los argumentos tal cual a `kubectl-venom_doctor` (ejecutable fijo, sin shell) y devuelve su código de
  salida. Si no está en el `PATH`, explica cómo instalarlo.
- `kdoctor` **sigue llamándose `kubectl-venom_doctor`**: krew exige ese nombre y es lo que habilita `kubectl venom-doctor`.
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

## Renombrado del plugin de kubectl (addendum, 2026-10-01)

El primer diseño llamaba al plugin `kubectl-doctor`. Al prepararlo para krew se comprobó que el índice ya tiene un plugin
`doctor` de otro proyecto (`emirozer/kubectl-doctor`), con el mismo nombre de binario: no se podía publicar, y quien
instalara ambos habría sufrido un choque de binarios (además, `kubectl krew install doctor` habría instalado el plugin
ajeno). Un intento intermedio usó `kubectl-venom`, pero la guía de nombres de krew pide nombres específicos que indiquen
la acción, y `venom` a secas no la indica. Se adopta **`venom-doctor`**: plugin `venom-doctor` en krew, comando
`kubectl venom-doctor` y binario `kubectl-venom_doctor` (kubectl exige guion bajo en el binario cuando el plugin lleva
guion). El paquete Go sigue llamándose `kdoctor` y `venom doctor` ejecuta `kubectl-venom_doctor`. Los archivos de las
versiones 0.1.0 y 0.1.1 llevan nombres antiguos; el cambio sale en la 0.1.2.

## Repositorio firmado (addendum, 2026-10-02)

Para instalar sin URL se añadió un generador de repositorio yum/dnf y apt con paquetes y metadatos firmados con GPG
(`packaging/repo/`), probado de extremo a extremo con una clave desechable: instalación real con las comprobaciones de firma
activas en cuatro distribuciones y rechazo, por el motivo correcto, de un cliente sin la clave y de paquetes o índices
manipulados. Se alojará en GitHub Pages en este mismo repositorio, mediante el workflow manual `publish-repo.yml`. **Aún no está
publicado**: falta que quien mantiene el repo genere y custodie la clave de firma real, y active Pages (ver `packaging/repo/README.md`).
