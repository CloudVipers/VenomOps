# venom-doctor

Plugin de `kubectl` (`kubectl venom-doctor`) que explica **en lenguaje claro por qué algo está roto** en un clúster
de Kubernetes/EKS y **cómo arreglarlo**. Es de **solo lectura**: nunca modifica el clúster.

Produce *findings* en el formato común de [`findings-schema`](../findings-schema), que luego puede corregir
[`pr-agent`](../pr-agent) y debatir [`arch-committee`](../arch-committee).

## Qué detecta

Nueve reglas. Para cada una, cómo reconocer el problema, confirmarlo a mano y arreglarlo: [catálogo de reglas](../../docs/guia/reglas.md).

| ID | Regla | Qué explica |
|---|---|---|
| `VD-K8S-001` | CrashLoopBackOff | Último exit code (con su significado), líneas finales del log y reinicios |
| `VD-K8S-002` | OOMKilled | Compara `limits.memory` con el uso (si hay metrics-server) y sugiere un nuevo valor |
| `VD-K8S-003` | ImagePullBackOff | Distingue imagen/tag inexistente, credenciales, *rate limit* y problemas de red |
| `VD-K8S-004` | Pending | Eventos del scheduler: recursos insuficientes, taints, selectores y PVC sin bind o inexistentes |
| `VD-K8S-005` | Probes fallando | Liveness/readiness/startup con eventos `Unhealthy` confirmados por el estado actual del contenedor; distingue puerto/ruta incorrectos, timeouts, 5xx y comandos fallidos, e incluye la configuración de la probe |
| `VD-K8S-006` | Nodos `NotReady` | Nodos que no están Ready (kubelet detenido, CNI, PLEG, runtime) y nodos Ready con presión de memoria, disco o PIDs; indica cuántos Pods se ven afectados. **Solo se ejecuta con `-A`** (los nodos son de todo el clúster y un namespace no debería requerir permiso para listarlos; con `-n` venom-doctor lo avisa en stderr) |
| `VD-K8S-007` | PDB que bloquea drains | PodDisruptionBudgets sin disrupciones permitidas (workload sano pero sin margen, Pods no sanos o `minAvailable` mayor que las réplicas) y selectores que no coinciden con nada; ignora los PDB que el controlador aún no procesó |
| `VD-K8S-008` | Karpenter sin capacidad | NodePools que no están Ready o que alcanzaron `spec.limits`, y NodeClaims atascados en `Launched`, `Registered` o `Initialized`. Muestra el reason y el mensaje que reporta cada objeto, sin interpretarlos. **Solo con `-A`**, necesita permiso de lectura sobre `nodepools` y `nodeclaims` de `karpenter.sh` y no hace nada si Karpenter no está instalado |
| `VD-K8S-009` | IRSA mal cableado | ServiceAccounts con la anotación `eks.amazonaws.com/role-arn` mal formada, y Pods que no recibieron las variables de IRSA (`AWS_ROLE_ARN`, `AWS_WEB_IDENTITY_TOKEN_FILE`) o llevan un rol desactualizado. Un finding por ServiceAccount; los IDs de cuenta se enmascaran. Comprueba la **consistencia dentro del clúster**, no las políticas de confianza ni los permisos del rol IAM, ni EKS Pod Identity (ver ADR 0008) |

Un contenedor terminado por OOM se reporta solo con `VD-K8S-002` (que trae la corrección), no dos veces.

## Instalación

```bash
# Paquete (instala también el comando `venom`): RHEL/Rocky/Alma/Amazon Linux o Debian/Ubuntu
# Instrucciones del repositorio firmado: ../../docs/guia/instalacion.md
sudo dnf install venom            # o: sudo apt install venom

# Solo este plugin, con krew (Linux, macOS y Windows)
kubectl krew install --manifest-url=https://raw.githubusercontent.com/CloudVipers/VenomOps/main/packages/venom-doctor/venom-doctor.yaml
kubectl venom-doctor --version
```

También hay un binario por plataforma en cada [release](https://github.com/CloudVipers/VenomOps/releases) (`venom-doctor_vX.Y.Z_<os>_<arch>`, con su
`checksums.txt`), y puedes compilarlo desde el monorepo con Go 1.26+:

```bash
cd packages/venom-doctor
make build                      # deja el binario en bin/kubectl-venom_doctor
sudo install bin/kubectl-venom_doctor /usr/local/bin/   # kubectl lo detecta como `kubectl venom-doctor`
```

El manifiesto de [krew](https://krew.sigs.k8s.io/) es [`venom-doctor.yaml`](venom-doctor.yaml) y está propuesto al índice oficial; los binarios de release se
generan con GoReleaser (`make snapshot` los construye sin publicar nada). Guía completa: [instalación](../../docs/guia/instalacion.md).

## Uso

```bash
kubectl venom-doctor                     # namespace del contexto actual
kubectl venom-doctor -n payments         # un namespace
kubectl venom-doctor -A                  # todos los namespaces
kubectl venom-doctor -A -o json          # salida JSON (array de findings válido contra findings-schema)
```

Acepta los flags habituales de kubectl (`--kubeconfig`, `--context`, `-n`, ...).

**Con `-n` no se revisan los nodos (`VD-K8S-006`) ni Karpenter (`VD-K8S-008`)**, porque son de todo el clúster; el comando lo avisa por `stderr`. Usa `-A` para el
diagnóstico completo.

Ejemplo (sobre los manifiestos de [`examples/k8s`](../../examples/k8s)):

```text
SEVERIDAD  ID          RECURSO                     PROBLEMA
HIGH       VD-K8S-001  Pod venom-demo/crashloop  CrashLoopBackOff en el container app
HIGH       VD-K8S-002  Pod venom-demo/oom        OOMKilled en el container app
HIGH       VD-K8S-003  Pod venom-demo/badimage   No se puede descargar la imagen del container app
HIGH       VD-K8S-004  Pod venom-demo/pending    Pod en Pending: el scheduler no puede programarlo

── [HIGH] VD-K8S-002 · OOMKilled en el container app
Recurso: Pod venom-demo/oom
Causa probable: El contenedor superó su límite de memoria de 32Mi y el kernel lo terminó (OOMKilled).
Evidencia:
  • last-state: container "app" Terminated reason=OOMKilled, exitCode=137, restartCount=6
  • limits: limits.memory=32Mi
Cómo arreglarlo (riesgo low): Subir limits.memory de 32Mi a 64Mi (punto de partida).
  1. Actualizar resources.limits.memory a 64Mi en el manifiesto del workload.
  2. Verificar que no vuelva a ocurrir y ajustar con el consumo observado; si crece sin parar, buscar una fuga de memoria.
  IaC: resources { limits = { memory = "64Mi" } }
  Ref: https://kubernetes.io/docs/tasks/configure-pod-container/assign-memory-resource/
```

### Salida JSON

`-o json` imprime un **array** de findings. Cada uno se valida contra `findings-schema` antes de escribirse:
si alguno incumpliera el contrato, el comando falla en vez de emitir un documento inválido. Los avisos
(reglas que no pudieron ejecutarse) van a `stderr`, así que `stdout` es siempre JSON puro.

## Integración con `pr-agent`

Los findings de OOMKilled (`VD-K8S-002`) traen una evidencia **parseable** `memory-limit-change`
(`container=app;from=32Mi;to=64Mi`) con la que [`pr-agent`](../pr-agent) puede subir `limits.memory` en el Terraform que
declara el workload:

```bash
kubectl venom-doctor -n payments -o json | pr-agent fix --finding - --supported --repo ./infra --dry-run
```

`--supported` elige, del array, el único finding que tiene arreglo automático. El formato de la evidencia es un contrato
documentado en el [ADR 0004](../../docs/decisiones/0004-convenciones-evidence.md).

## Explicación con IA (`--explain`, opcional)

Desactivada por defecto. Si la activas, cada finding se envía a **Amazon Bedrock** (API Converse) para añadir
una explicación ampliada, que aparece como evidencia de tipo `ai-explanation`:

```bash
export VENOM_DOCTOR_BEDROCK_MODEL=<modelId>      # o: --explain-model <modelId>
kubectl venom-doctor -n payments --explain
```

- **Sin flag no se hace ninguna llamada de IA.** Con el flag pero sin modelo o sin credenciales de AWS, se
  muestra un aviso y el diagnóstico continúa sin la explicación.
- **Se redactan los secretos antes de enviar nada:** claves de AWS, JWT, contraseñas/tokens en pares
  `clave=valor`, credenciales en URLs, llaves privadas y IDs de cuenta de 12 dígitos. Lo mismo se aplica a
  las líneas de log y eventos que aparecen en los findings.
- El modelo no tiene un valor por defecto a propósito (decisión pendiente en `CLAUDE.md`, sección 11).
- Usa la cadena estándar de credenciales de AWS (`AWS_PROFILE`, variables de entorno, rol, etc.).

## Seguridad

- **Solo lectura:** el lector de clúster solo expone `get`/`list` (y lectura de logs); hay un test que
  verifica que no se emite ningún otro verbo.
- Los logs y mensajes de eventos se **redactan** antes de entrar en un finding, porque los findings viajan a
  otros sistemas (PRs, prompts).
- **Permisos mínimos:** solo `get` y `list`. Un [`ClusterRole` de referencia](../../examples/rbac/venom-doctor-reader.yaml) cubre todas las reglas y está probado
  (suplantando a un usuario que solo tiene ese rol, no aparece ningún error de permisos): `pods`, `pods/log`, `events`, `persistentvolumeclaims`, `serviceaccounts` y `nodes`; `poddisruptionbudgets`
  (`policy`); opcionalmente `pods` de `metrics.k8s.io`; y `nodepools` y `nodeclaims` de `karpenter.sh` si usas Karpenter. Si falta un permiso, la regla afectada se salta con un aviso
  y las demás siguen; `events` y `pods/log` son contexto opcional y su ausencia solo resta evidencia, sin avisar.

## Desarrollo

```bash
make lint     # gofmt, go vet y golangci-lint (si está instalado; CI lo ejecuta siempre)
make test     # go test -race -cover ./...
make build
make snapshot # GoReleaser en modo snapshot (requiere goreleaser)
```

Estructura: `cmd/kubectl-venom_doctor` (CLI), `internal/engine` (motor de reglas concurrente),
`internal/cluster` (lector de solo lectura sobre `client-go`), `internal/rules` (una regla por archivo),
`internal/output` (tabla y JSON), `internal/explain` (Bedrock opcional) e `internal/redact` (enmascarado de
secretos). Las reglas se prueban con `fake.Clientset`.

### Probar contra un clúster kind

Usa un kubeconfig dedicado para no tocar tus contextos habituales. **Nunca apliques los manifiestos de
ejemplo en un clúster real.**

```bash
export KUBECONFIG=/tmp/venom-doctor-kind.kubeconfig
kind create cluster --name venom-doctor-test --kubeconfig "$KUBECONFIG"
kubectl apply -f ../../examples/k8s/        # ver examples/k8s/README.md (hay un escenario por regla)
sleep 120 && ./bin/kubectl-venom_doctor -n venom-demo
kind delete cluster --name venom-doctor-test --kubeconfig "$KUBECONFIG"
```

## Limitaciones conocidas

- Es una fotografía del momento en que se ejecuta: los eventos de Kubernetes duran cerca de una hora y un Pod ya recuperado no deja rastro. No es un sistema de monitorización ni sustituye la observabilidad.
- Las reglas de nodos y de Karpenter solo corren con `-A`; la de Karpenter no hace nada si no está instalado y nunca se ha probado contra un controlador de Karpenter en ejecución
  (sí contra un API server con los CRDs oficiales).
- `VD-K8S-009` comprueba la **consistencia de IRSA dentro del clúster**, no las políticas de confianza ni los permisos del rol IAM, y no cubre EKS Pod Identity (ver el
  [ADR 0008](../../docs/decisiones/0008-regla-irsa-venom-doctor.md)).
- Solo `VD-K8S-002` tiene arreglo automático con `pr-agent`; el resto pide intervención humana a propósito.
- El texto de los findings está en español; el código, los errores y los identificadores, en inglés.
- El uso de memoria para `OOMKilled` requiere metrics-server; sin él se sugiere duplicar el límite actual.
