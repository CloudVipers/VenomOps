# Uso

`venom` es un solo comando con tres verbos. Cada uno hace una cosa y se puede encadenar con los otros mediante un formato
común, el **finding**: un hallazgo con su severidad, evidencia, causa probable y pasos para arreglarlo.

| Verbo | Qué hace | Toca algo | Usa IA |
|---|---|---|---|
| [`venom doctor`](#venom-doctor-diagnosticar-un-clúster) | Explica por qué algo está roto en un clúster y cómo arreglarlo | No, solo lee | Solo con `--explain` |
| [`venom fix`](#venom-fix-del-hallazgo-al-pull-request) | Convierte un hallazgo en un Pull Request con el cambio mínimo en Terraform | Abre un PR (nunca lo mezcla) | Solo con `--agent` |
| [`venom review`](#venom-review-revisar-un-plan-de-terraform) | Un comité de agentes revisa un plan de Terraform y debate | Escribe un informe local | Sí (necesita Bedrock) |
| [`venom update`](#venom-update-actualizar-venom) | Descarga la última versión de venom, la verifica y, si lo pides, la instala | Solo con `--install` | No |

## `venom doctor`: diagnosticar un clúster

Ejecuta `kubectl-venom_doctor` (el mismo binario que el plugin `kubectl venom-doctor`). Es de **solo lectura**: nunca modifica el
clúster.

```bash
venom doctor                       # namespace del contexto actual
venom doctor -n pagos              # un namespace
venom doctor -A                    # todo el clúster (incluye nodos y Karpenter)
venom doctor -A -o json            # salida JSON: un array de findings
kubectl venom-doctor -n pagos      # equivalente, como plugin de kubectl
```

| Opción | Para qué sirve |
|---|---|
| `-n`, `--namespace` | Revisa un namespace |
| `-A`, `--all-namespaces` | Revisa todos. **Necesario para las reglas de nodos (`VD-K8S-006`) y Karpenter (`VD-K8S-008`)**, que son de todo el clúster |
| `-o`, `--output` | `table` (por defecto, para leer) o `json` (para encadenar con otras herramientas) |
| `--explain` | Añade una explicación ampliada generada con Amazon Bedrock (opcional) |
| `--explain-model` | Modelo de Bedrock para `--explain` (o la variable `VENOM_DOCTOR_BEDROCK_MODEL`) |
| `--kubeconfig`, `--context`, `--as`… | Las opciones habituales de `kubectl` |

### Cómo leer el resultado

Primero una tabla ordenada por gravedad, y debajo el detalle de cada hallazgo. Esto es la salida real sobre los manifiestos de
ejemplo de [`examples/k8s`](https://github.com/CloudVipers/VenomOps/tree/main/examples/k8s):

```text
SEVERIDAD  ID          RECURSO                                      PROBLEMA
HIGH       VD-K8S-001  Pod venom-demo/crashloop                     CrashLoopBackOff en el container app
HIGH       VD-K8S-002  Pod venom-demo/oom                           OOMKilled en el container app
HIGH       VD-K8S-003  Pod venom-demo/badimage                      No se puede descargar la imagen del container app
HIGH       VD-K8S-004  Pod venom-demo/pending                       Pod en Pending: el scheduler no puede programarlo
HIGH       VD-K8S-009  ServiceAccount venom-demo/irsa-mal           El ServiceAccount irsa-mal tiene un ARN de rol mal formado
MEDIUM     VD-K8S-007  PodDisruptionBudget venom-demo/pdb-greedy    El PDB pdb-greedy exige más Pods de los que existen
LOW        VD-K8S-007  PodDisruptionBudget venom-demo/pdb-orphan    El selector del PDB pdb-orphan no coincide con ningún Pod
```

Cada hallazgo tiene siempre las mismas partes:

```text
── [HIGH] VD-K8S-001 · CrashLoopBackOff en el container app
Recurso: Pod venom-demo/crashloop
Causa probable: La aplicación terminó con un error (código 1). Las últimas líneas del log suelen indicar el motivo.
Evidencia:
  • pod-status: container "app" crash-looping, restartCount=4
  • exit-code: Last state: Terminated, exitCode=1, reason="Error"
  • log-tail: fatal: missing required env DB_HOST
Cómo arreglarlo (riesgo low): Corregir la causa de la caída que muestran el código de salida y los logs.
  1. kubectl logs venom-demo/crashloop -c app --previous para ver el log de la última caída.
  2. kubectl describe pod venom-demo/crashloop para revisar eventos, probes y variables de entorno.
  3. Corregir configuración, imagen o comando según la causa y volver a desplegar.
```

- **Severidad:** `critical`, `high`, `medium`, `low` o `info`. Empieza por los `HIGH`.
- **Evidencia:** lo que se observó de verdad en el clúster. No son suposiciones.
- **Causa probable:** la explicación en lenguaje claro. Se llama «probable» porque el diagnóstico es una inferencia de la evidencia.
- **Riesgo:** cuánto riesgo tiene aplicar el arreglo sugerido (`low`, `medium` o `high`).

> **Aviso importante.** Con `-n`, venom-doctor no revisa los nodos ni Karpenter y te lo dice por `stderr`:
> *«nota: con un namespace (-n) no se revisan los nodos ni Karpenter; usa -A…»*. Un resultado limpio con `-n` no significa que
> todo el clúster esté bien.

### Explicación con IA (opcional)

Con `--explain`, cada hallazgo se envía a Amazon Bedrock para añadir una explicación ampliada. **Está desactivado por defecto**
y los secretos se enmascaran antes de enviar nada:

```bash
export VENOM_DOCTOR_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0
venom doctor -n pagos --explain
```

```text
  • ai-explanation:
      ## Explicación del Hallazgo

      **El problema:** El contenedor `app` en el Pod `oom` fue terminado por el kernel (OOMKilled) porque consumió más memoria
      de la permitida (32Mi). Ya se reinició 4 veces.

      **Siguiente paso:** Aumentar `limits.memory` de 32Mi a 64Mi en el manifiesto del workload y redeploy. Luego monitorear si
      el problema persiste; si sigue creciendo, investigar si hay una fuga de memoria.
```

No hay un modelo por defecto: lo eliges tú (es una decisión de coste, región y disponibilidad en tu cuenta). Detalles en el
[ADR 0005](https://github.com/CloudVipers/VenomOps/blob/main/docs/decisiones/0005-modelos-bedrock.md).

### Permisos que necesita en el clúster

Solo `get` y `list`; ni un permiso de escritura. Este `ClusterRole` ([`examples/rbac`](https://github.com/CloudVipers/VenomOps/blob/main/examples/rbac/venom-doctor-reader.yaml))
cubre todas las reglas y está probado: con él no aparece ningún error de permisos.

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: venom-doctor-reader
rules:
  - apiGroups: [""]
    resources: [pods, pods/log, events, persistentvolumeclaims, serviceaccounts, nodes]
    verbs: [get, list]
  - apiGroups: [policy]
    resources: [poddisruptionbudgets]
    verbs: [get, list]
  - apiGroups: [metrics.k8s.io]
    resources: [pods]
    verbs: [get, list]
  - apiGroups: [karpenter.sh]
    resources: [nodepools, nodeclaims]
    verbs: [get, list]
```

Si falta un permiso necesario, la regla afectada se salta con un aviso claro y las demás siguen:

```text
aviso: rule VD-K8S-009: list serviceaccounts: serviceaccounts is forbidden: User "alguien" cannot list resource "serviceaccounts" in API group "" at the cluster scope
```

Dos matices: `events` y `pods/log` son **contexto opcional**: si faltan, el diagnóstico continúa pero con menos evidencia y sin avisar.
`metrics.k8s.io` solo sirve para sugerir un límite de memoria más ajustado en `VD-K8S-002`.

## `venom fix`: del hallazgo al Pull Request

Toma **un** hallazgo y prepara el cambio mínimo en tu Terraform. Siempre pasa por una persona: abre un PR y **nunca lo mezcla**.

```bash
# Empieza siempre con --dry-run: muestra el diff y el cuerpo del PR sin tocar nada
venom doctor -n pagos -o json | venom fix --finding - --supported --repo ./infra --dry-run
```

```text
Finding VD-K8S-002: Subir limits.memory del contenedor `app` de 32Mi a 64Mi en `kubernetes_pod_v1.oom`.
Branch:  fix/vd-k8s-002-oom
Files:   main.tf
Risk:    low

--- diff (dry-run) ---
--- a/main.tf
+++ b/main.tf
@@ -18,7 +18,7 @@
           memory = "16Mi"
         }
         limits = {
-          memory = "32Mi"
+          memory = "64Mi"
         }

--- PR title ---
fix(terraform): Subir limits.memory del contenedor app de 32Mi a 64Mi en kubernetes_pod_v1.oom [VD-K8S-002]
```

Cuando el diff te convenza, quita `--dry-run` (y exporta `GITHUB_TOKEN`): crea la rama `fix/…`, valida con `terraform validate` y
`terraform plan`, hace el commit y abre el PR con el hallazgo original, el diff, el plan y una lista de revisión humana.

| Opción | Para qué sirve |
|---|---|
| `--finding` | Archivo JSON con el hallazgo (o un array), o `-` para leer de la entrada estándar |
| `--repo` | Carpeta de Terraform (dentro de un repositorio git para abrir el PR) |
| `--dry-run` | Muestra el diff y el cuerpo del PR sin tocar GitHub ni el repositorio |
| `--supported` | De un array, elige **el único** hallazgo que tiene arreglo automático |
| `--id`, `--resource`, `--index` | Otras formas de elegir un hallazgo de un array |
| `--skip-plan`, `--require-plan` | No ejecutar `terraform plan`, o abortar si falla |
| `--github-repo`, `--base` | `owner/nombre` y rama base del PR (por defecto, los del remoto `origin`) |
| `--agent`, `--model-id`, `--region` | Agente de IA opcional para hallazgos sin arreglo determinista (necesita Bedrock) |

### Qué arregla de forma automática

| Hallazgo | Cambio |
|---|---|
| `VD-K8S-002` Pod terminado por OOMKilled | Sube `limits.memory` en el Terraform que declara el workload |
| `TF-S3-001` Bucket S3 sin cifrado | Añade el cifrado del bucket |
| `TF-TAG-001` Etiquetas obligatorias faltantes | Añade las etiquetas que faltan |
| `TF-EBS-001` Volumen EBS gp2 | Cambia el tipo a gp3 |

Los hallazgos `TF-*` los escribe una persona o los produce otra herramienta (por ejemplo un escáner de Terraform): `venom fix`
acepta cualquier finding que cumpla el [contrato común](https://github.com/CloudVipers/VenomOps/tree/main/packages/findings-schema).

Para cualquier otro hallazgo no hay arreglo determinista: `venom fix` te lo dice y, si quieres, puedes probar el agente opcional
con `--agent --model-id <modelo>`. **El resto de reglas de `venom doctor` piden intervención humana**, a propósito: cambiar un
probe, un PDB o un rol IAM sin entender el contexto es justo lo que no queremos automatizar.

### Garantías

- Trabaja en una **copia temporal**: en `--dry-run` tu repositorio queda exactamente igual.
- Solo puede ejecutar `terraform init/validate/plan`; **ningún `apply`**. Está bloqueado en el código, no solo en las instrucciones.
- Si el cambio toca archivos que no declaró, aborta. El diff debe ser mínimo.

## `venom review`: revisar un plan de Terraform

Un **comité virtual** de cuatro especialistas (seguridad, costes, fiabilidad y operaciones) analiza el plan, se cuestionan entre sí
y un moderador consolida, prioriza y deja constancia explícita de los **desacuerdos**. No aplica nada.

```bash
terraform plan -out plan.out && terraform show -json plan.out > plan.json
venom review --plan plan.json --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 --out ./informe
```

Escribe `informe/report.md` (para personas) e `informe/findings.json` (para máquinas). Antes de gastar nada, mira exactamente qué
se enviaría (ya con los secretos enmascarados) con `--dry-run`:

```text
Plan: Terraform 1.16.3, 6 resources (6 create)
Types: aws_db_instance x1, aws_db_subnet_group x1, aws_security_group x1, aws_subnet x2, aws_vpc x1

--- Dry run: no model is called and nothing is written ---
Agents: security, cost, reliability, operations + moderator; rounds: 2
  round 1 · security: ~1,405 input tokens
  round 1 · cost: ~1,380 input tokens
Budget: 200,000 tokens
```

| Opción | Para qué sirve |
|---|---|
| `--plan` | Salida de `terraform show -json` |
| `--out` | Carpeta de salida (`report.md` y `findings.json`) |
| `--model-id`, `--region` | Modelo de Bedrock (obligatorio, sin valor por defecto) y región |
| `--max-tokens`, `--max-rounds` | Presupuesto total de tokens (200 000 por defecto) y rondas: 1 = solo análisis, 2 = con réplica |
| `--context-findings` | Hallazgos previos (por ejemplo la salida de `venom doctor -o json`) para contrastar el plan con lo que pasa en producción |
| `--dry-run` | Muestra lo que se enviaría, sin llamar a ningún modelo |

## `venom update`: actualizar venom

Comprueba si hay una versión nueva, **descarga el paquete de tu sistema y arquitectura y lo verifica** antes de nada. No instala nada a menos que se lo pidas.

```bash
venom update --check       # ¿hay versión nueva? No descarga nada (sale con código 100 si la hay, como `dnf check-update`)
venom update               # descarga el paquete, lo verifica y te dice cómo instalarlo
venom update --install     # además lo instala con tu gestor de paquetes (te pregunta antes y usa sudo)
```

```text
Installed: 0.1.5
Latest:    0.1.6
Downloading venom-0.1.6-1.x86_64.rpm ...
Verifying the signature with the VenomOps key pinned in venom ...
Verified: ./venom-0.1.6-1.x86_64.rpm
Not installed. To install it:
  sudo dnf install -y /home/ana/venom-0.1.6-1.x86_64.rpm
or run: venom update --install
```

**Cómo sabe que el paquete es legítimo.** La clave pública de VenomOps (huella `A7BE 1F5E 03EC 7AA9 C797 A9DE 3237 E8D7 9E6E 29C5`) viaja **dentro de `venom`**, no se descarga de internet,
así que quien controle el sitio no puede cambiarla. Con ella se comprueba la firma: en un `.rpm`, la del propio paquete (con una base de datos de `rpm` temporal; la de tu sistema no se toca) y en un `.deb`, la cadena que sigue `apt`
(índice firmado → hash de `Packages.gz` → hash del `.deb`). Si algo no cuadra, **se borra el paquete y el comando falla**, aunque pases `--install --yes`. Está probado con paquetes manipulados y con un
`latest.json` regenerado para que sus hashes coincidan con el paquete manipulado.

| Opción | Para qué sirve |
|---|---|
| `--check` | Solo comprueba la versión; no descarga nada |
| `--install` | Instala el paquete **verificado** con `dnf`/`yum` o `apt-get`, usando `sudo` si no eres root. Muestra el comando exacto y pide confirmación |
| `-y`, `--yes` | Con `--install`, no pide confirmación (para scripts) |
| `--force` | Descarga y verifica aunque ya tengas la última versión (útil para reinstalar) |
| `--dir` | Dónde guardar el paquete (por defecto, la carpeta actual) |

Detalles: solo funciona en Linux (en macOS o Windows, actualiza el plugin con krew o el binario); usa solo HTTPS y rechaza redirecciones que salgan de HTTPS; y si instalaste `venom` desde el
[repositorio firmado](instalacion.md#paquete-rpm-o-deb-recomendado), `sudo dnf upgrade venom` o `sudo apt install --only-upgrade venom` hacen lo mismo con la verificación del propio gestor de paquetes.

## Encadenarlos

Todo habla el mismo formato, así que se pueden combinar:

```bash
# Diagnosticar, guardar el resultado y revisar un plan teniendo en cuenta lo que pasa en el clúster
venom doctor -A -o json > hallazgos.json
venom review --plan plan.json --context-findings hallazgos.json --model-id <modelo> --out ./informe

# Solo los hallazgos graves, con jq
venom doctor -A -o json | jq '[.[] | select(.severity=="critical" or .severity=="high")]'
```

Más combinaciones, con situaciones reales, en [casos de uso](casos-de-uso.md).

## Variables de entorno

| Variable | Qué hace |
|---|---|
| `VENOM_DOCTOR_BEDROCK_MODEL` | Modelo de Bedrock para `venom doctor --explain` |
| `PR_AGENT_BEDROCK_MODEL` | Modelo de Bedrock para `venom fix --agent` |
| `ARCH_COMMITTEE_BEDROCK_MODEL` | Modelo de Bedrock para `venom review` |
| `GITHUB_TOKEN` | Solo para que `venom fix` abra el PR (no hace falta con `--dry-run`) |
| `AWS_PROFILE`, `AWS_REGION`… | Las credenciales y la región de AWS habituales, para las funciones con Bedrock |
