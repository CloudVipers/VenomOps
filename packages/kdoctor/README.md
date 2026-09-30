# kdoctor

Plugin de `kubectl` (`kubectl doctor`) que explica **en lenguaje claro por qué algo está roto** en un clúster
de Kubernetes/EKS y **cómo arreglarlo**. Es de **solo lectura**: nunca modifica el clúster.

Produce *findings* en el formato común de [`findings-schema`](../findings-schema), que luego puede corregir
[`pr-agent`](../pr-agent) y debatir [`arch-committee`](../arch-committee).

## Qué detecta (MVP)

| ID | Regla | Qué explica |
|---|---|---|
| `KD-K8S-001` | CrashLoopBackOff | Último exit code (con su significado), líneas finales del log y reinicios |
| `KD-K8S-002` | OOMKilled | Compara `limits.memory` con el uso (si hay metrics-server) y sugiere un nuevo valor |
| `KD-K8S-003` | ImagePullBackOff | Distingue imagen/tag inexistente, credenciales, *rate limit* y problemas de red |
| `KD-K8S-004` | Pending | Eventos del scheduler: recursos insuficientes, taints, selectores y PVC sin bind o inexistentes |

Un contenedor terminado por OOM se reporta solo con `KD-K8S-002` (que trae la corrección), no dos veces.

## Instalación

Todavía no hay release público. Por ahora, desde el monorepo (Go 1.26+):

```bash
cd packages/kdoctor
make build                      # deja el binario en bin/kubectl-doctor
sudo install bin/kubectl-doctor /usr/local/bin/   # kubectl lo detecta como `kubectl doctor`
```

La distribución por [krew](https://krew.sigs.k8s.io/) está preparada en [`kdoctor.yaml`](kdoctor.yaml) y se
activará cuando exista un release público (los `sha256` se rellenan entonces). Los binarios de release se
generan con GoReleaser (`make snapshot` los construye sin publicar nada).

## Uso

```bash
kubectl doctor                     # namespace del contexto actual
kubectl doctor -n payments         # un namespace
kubectl doctor -A                  # todos los namespaces
kubectl doctor -A -o json          # salida JSON (array de findings válido contra findings-schema)
```

Acepta los flags habituales de kubectl (`--kubeconfig`, `--context`, `-n`, ...).

Ejemplo (sobre los manifiestos de [`examples/k8s`](../../examples/k8s)):

```text
SEVERIDAD  ID          RECURSO                     PROBLEMA
HIGH       KD-K8S-001  Pod kdoctor-demo/crashloop  CrashLoopBackOff en el container app
HIGH       KD-K8S-002  Pod kdoctor-demo/oom        OOMKilled en el container app
HIGH       KD-K8S-003  Pod kdoctor-demo/badimage   No se puede descargar la imagen del container app
HIGH       KD-K8S-004  Pod kdoctor-demo/pending    Pod en Pending: el scheduler no puede programarlo

── [HIGH] KD-K8S-002 · OOMKilled en el container app
Recurso: Pod kdoctor-demo/oom
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

## Explicación con IA (`--explain`, opcional)

Desactivada por defecto. Si la activas, cada finding se envía a **Amazon Bedrock** (API Converse) para añadir
una explicación ampliada, que aparece como evidencia de tipo `ai-explanation`:

```bash
export KDOCTOR_BEDROCK_MODEL=<modelId>      # o: --explain-model <modelId>
kubectl doctor -n payments --explain
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
- Permisos mínimos que necesita el usuario: `get/list` sobre `pods`, `pods/log`, `events` y
  `persistentvolumeclaims`, y opcionalmente `pods.metrics.k8s.io`.

## Desarrollo

```bash
make lint     # gofmt, go vet y golangci-lint (si está instalado; CI lo ejecuta siempre)
make test     # go test -race -cover ./...
make build
make snapshot # GoReleaser en modo snapshot (requiere goreleaser)
```

Estructura: `cmd/kubectl-doctor` (CLI), `internal/engine` (motor de reglas concurrente),
`internal/cluster` (lector de solo lectura sobre `client-go`), `internal/rules` (una regla por archivo),
`internal/output` (tabla y JSON), `internal/explain` (Bedrock opcional) e `internal/redact` (enmascarado de
secretos). Las reglas se prueban con `fake.Clientset`.

### Probar contra un clúster kind

Usa un kubeconfig dedicado para no tocar tus contextos habituales. **Nunca apliques los manifiestos de
ejemplo en un clúster real.**

```bash
export KUBECONFIG=/tmp/kdoctor-kind.kubeconfig
kind create cluster --name kdoctor-test --kubeconfig "$KUBECONFIG"
kubectl apply -f ../../examples/k8s/
sleep 90 && ./bin/kubectl-doctor -n kdoctor-demo
kind delete cluster --name kdoctor-test --kubeconfig "$KUBECONFIG"
```

## Limitaciones conocidas

- Solo Pods (los workloads se infieren por sus Pods); segunda tanda de reglas pendiente: probes fallando,
  IRSA / EKS Pod Identity, nodos `NotReady`, PDB que bloquea drains y Karpenter sin capacidad.
- El texto de los findings está en español; el código, los errores y los identificadores, en inglés.
- El uso de memoria para `OOMKilled` requiere metrics-server; sin él se sugiere duplicar el límite actual.
