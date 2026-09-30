# pr-agent

Toma un *finding* (formato común de [`findings-schema`](../findings-schema)) y abre un **Pull Request con el
cambio mínimo en Terraform** que lo corrige. **Nunca hace merge** y nunca aplica nada: un humano revisa y mezcla.

## Flujo

```
finding.json ──▶ validar contra findings-schema
                        │
                        ▼
              fixer determinista por id  (o agente LLM opcional con --agent)
                        │   trabaja en una COPIA temporal del repo (sandbox)
                        ▼
        diff mínimo verificado ──▶ terraform validate ──▶ terraform plan
                        │
            ┌───────────┴────────────┐
        --dry-run                 sin --dry-run
   imprime diff + cuerpo      rama fix/<id>-<recurso> → commit → push → Pull Request
   del PR; no toca nada       (el repo solo se modifica cuando todo lo anterior pasó)
```

## Fixers incluidos (MVP)

| ID del finding | Qué corrige | Cómo |
|---|---|---|
| `TF-S3-001` | Bucket S3 sin cifrado | Agrega `aws_s3_bucket_server_side_encryption_configuration` (SSE-S3, AES256) |
| `TF-TAG-001` | Tags obligatorios faltantes | Agrega solo los tags que faltan (nunca pisa un valor existente) |
| `TF-EBS-001` | Volumen gp2 | Cambia a gp3 en `aws_ebs_volume`, `aws_instance` o `aws_launch_template`; avisa si el volumen es grande |
| `KD-K8S-002` (de `kdoctor`) | Contenedor terminado por OOMKilled | Sube `limits.memory` del contenedor en el recurso `kubernetes_*` de Terraform que declara el Pod/Deployment/StatefulSet... (solo ese `limits`, nunca `requests`) |

Para **`TF-TAG-001`** el finding debe traer los valores en una evidencia `required-tags` con
`detail: "Environment=prod;Owner=team-a"`. Todos son idempotentes: si el código ya tiene el arreglo, no hace nada.

## Uso

```bash
make setup                         # crea .venv (instala antes ../findings-schema/python)
. .venv/bin/activate

# Ver qué haría, sin tocar nada (no necesita GitHub):
pr-agent fix --finding ../../examples/findings/s3-no-encryption.json \
             --repo ../../examples/terraform/s3-demo --dry-run

# Abrir el PR de verdad (el directorio debe estar en un repo git con remoto 'origin'):
export GITHUB_TOKEN=...            # solo para abrir el PR
pr-agent fix --finding finding.json --repo ./infra
```

El finding puede ser un archivo, un **array** (la salida de `kdoctor -o json`; elige con `--index`) o `-` por stdin.
### Desde `kdoctor` (tubería)

`kdoctor -o json` emite un **array**; para elegir un finding usa `--id`, `--resource` (`nombre` o `namespace/nombre`),
`--index` o `--supported` (el único con arreglo automático):

```bash
kubectl doctor -n kdoctor-demo -o json | pr-agent fix --finding - --supported \
    --repo ../../examples/terraform/k8s-oom-demo --dry-run
```

`KD-K8S-002` se asocia al recurso de Terraform por `metadata.name`/`namespace` literales (un Deployment se deduce por el
nombre del Pod) y necesita la evidencia `memory-limit-change` ([ADR 0004](../../docs/decisiones/0004-convenciones-evidence.md)).
Si no hay coincidencia única, o el valor no es literal, se detiene en lugar de adivinar. Demo completa:
[`examples/demo`](../../examples/demo).

Otras opciones: `--skip-plan`, `--require-plan`, `--github-repo owner/nombre`, `--base main`.

Ejemplo real (`--dry-run` sobre `examples/terraform/s3-demo`, con `terraform validate` y `plan` ejecutados):

```text
Finding TF-S3-001: Habilitar cifrado SSE-S3 (AES256) en el bucket `demo_logs`.
Branch:  fix/tf-s3-001-demo-logs
Files:   main.tf
Risk:    low

--- diff (dry-run) ---
--- a/main.tf
+++ b/main.tf
@@ -14,3 +14,13 @@
     status = "Enabled"
   }
 }
+
+resource "aws_s3_bucket_server_side_encryption_configuration" "demo_logs" {
+  bucket = aws_s3_bucket.demo_logs.id
+
+  rule {
+    apply_server_side_encryption_by_default {
+      sse_algorithm = "AES256"
+    }
+  }
...
```

## Seguridad (se hace cumplir en código, no solo en el prompt)

Ver [ADR 0002](../../docs/decisiones/0002-pr-agent-seguridad.md).

- **Lista de comandos permitidos** (`safety.py`): solo `terraform init -backend=false`, `validate` y `plan`, y un
  subconjunto mínimo de `git`. `terraform apply/destroy/import/state/...`, cualquier otro programa, `git push
  --force`, push a `main`/`master`/ramas protegidas, `reset --hard`, `merge` o `add -A` lanzan una excepción
  **antes** de ejecutar nada (hay más de 60 casos de prueba).
- **Rutas confinadas al repo:** se rechazan `..`, rutas absolutas, symlinks que escapan, `.git/`, `.terraform/`,
  `*.tfstate`, `*.tfvars`, `.env` y llaves; los archivos de secretos ni siquiera aparecen al listar.
- **Edición estructurada y verificada:** solo tres operaciones (`append_block`, `replace_attr`, `ensure_tags`); el
  resultado se reparsea con `python-hcl2` y, si no es HCL válido, no se escribe nada.
- **Diff mínimo verificado:** si el arreglo toca archivos no declarados, crea o borra archivos, o el diff supera 80
  líneas, se aborta.
- **Sandbox:** todo se hace en una copia temporal; en `--dry-run` el repo real queda exactamente igual (sin
  `.terraform` ni lock file). Si falla algo antes del commit, se restaura todo y se borra la rama creada.
- **Sin merge:** el cliente de GitHub solo sabe leer la rama por defecto y **abrir** PRs; un test verifica que no
  existe ninguna operación de merge/aprobación/cierre. El PR lleva un checklist de revisión humana.
- **Secretos:** el entorno de los procesos hijos se limpia (solo `PATH`, `TF_*`, `AWS_*`...), y el plan, la evidencia
  y todo lo enviado a un LLM pasan por un redactor de secretos.

### Agente LLM (opcional)

Desactivado por defecto. Para findings sin fixer determinista: `--agent --model-id <modelId>` (o la variable
`PR_AGENT_BEDROCK_MODEL`; **no hay modelo por defecto**, ver [ADR 0005](../../docs/decisiones/0005-modelos-bedrock.md); probado con `us.anthropic.claude-haiku-4-5-20251001-v1:0`). Usa Amazon Bedrock
(Converse con *tool use*) y solo puede actuar mediante las cinco herramientas (`read_file`, `list_files`,
`edit_hcl`, `terraform_validate`, `terraform_plan`), de modo que los límites anteriores aplican igual aunque el
modelo pida otra cosa; después se exige `terraform validate` y el diff mínimo como a cualquier fixer.

## Desarrollo

```bash
make lint    # ruff, ruff format --check, mypy --strict
make test    # pytest (incluye pruebas con Terraform real si está instalado)
```

Las pruebas con Terraform real descargan el provider de AWS (usa `TF_PLUGIN_CACHE_DIR` para reutilizarlo) y se
omiten con `PR_AGENT_SKIP_TERRAFORM=1` o si `terraform` no está en el `PATH`. Estructura: `safety.py`, `hcl.py`,
`tools/`, `fixes/` (un fixer por tipo), `workflow.py`, `pr_body.py`, `github_client.py`, `agent.py`, `cli.py`.

## Limitaciones conocidas

- `terraform init -backend=false` es necesario para que `validate` cargue los providers; es el único comando fuera de
  la lista original de `CLAUDE.md` (ver ADR 0002) y nunca toca el estado remoto.
- `KD-K8S-002` necesita `metadata.name`/`namespace` literales y un único recurso que coincida; no usa variables ni `for_each`.
- Tags: solo edita mapas `tags = { ... }` literales de varias líneas; si son una variable o `merge(...)` se detiene.
- La copia de trabajo no incluye `*.tfvars` (pueden tener secretos): en repos con variables obligatorias el `plan` puede no
  estar disponible y el PR lo dice; `validate` sí se ejecuta siempre.
- Terraform evalúa la configuración al hacer `init`/`plan`: úsalo solo sobre repos de confianza (ver ADR 0002).
- Para abrir el PR el árbol de trabajo debe estar limpio y el directorio dentro de un repo git con remoto GitHub.
