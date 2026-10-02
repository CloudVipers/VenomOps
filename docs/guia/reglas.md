# Catálogo de reglas

`venom doctor` aplica nueve reglas, `VD-K8S-001` a `VD-K8S-009`. Esta página explica cada una con el mismo esquema: **qué
significa**, **cómo se ve**, **qué evidencia aporta**, **cómo confirmarlo tú a mano** (con comandos de solo lectura) y **cómo arreglarlo**.

| ID | Regla | Qué cazas | Alcance | Se corrige con `venom fix` |
|---|---|---|---|---|
| [`VD-K8S-001`](#vd-k8s-001-crashloopbackoff) | CrashLoopBackOff | Un contenedor arranca, falla y se reinicia en bucle | `-n` o `-A` | No |
| [`VD-K8S-002`](#vd-k8s-002-oomkilled) | OOMKilled | El kernel mató el contenedor por superar su memoria | `-n` o `-A` | **Sí** |
| [`VD-K8S-003`](#vd-k8s-003-imagepullbackoff) | ImagePullBackOff | No se puede descargar la imagen | `-n` o `-A` | No |
| [`VD-K8S-004`](#vd-k8s-004-pending) | Pending | El scheduler no encuentra dónde colocar el Pod | `-n` o `-A` | No |
| [`VD-K8S-005`](#vd-k8s-005-probes-fallando) | Probes fallando | Liveness, readiness o startup fallan | `-n` o `-A` | No |
| [`VD-K8S-006`](#vd-k8s-006-nodos-notready) | Nodos NotReady | Un nodo no está listo o tiene presión de recursos | solo `-A` | No |
| [`VD-K8S-007`](#vd-k8s-007-pdb-que-bloquea-drains) | PDB que bloquea drains | Un PodDisruptionBudget impide drenar nodos | `-n` o `-A` | No |
| [`VD-K8S-008`](#vd-k8s-008-karpenter-sin-capacidad) | Karpenter sin capacidad | NodePools o NodeClaims que no consiguen nodos | solo `-A` | No |
| [`VD-K8S-009`](#vd-k8s-009-irsa-mal-cableado) | IRSA mal cableado | Pods sin credenciales de IAM por su ServiceAccount | `-n` o `-A` | No |

**Por dónde empezar.** Ordena por severidad. Un `HIGH` es algo que probablemente ya afecta a usuarios o a una operación (un Pod que
no arranca, un nodo caído). Un `MEDIUM` suele ser una mina que aún no ha explotado (un PDB que hará colgar el próximo
mantenimiento). Un `LOW` es higiene.

---

## VD-K8S-001: CrashLoopBackOff

**Qué significa.** El contenedor arranca, termina con un error y Kubernetes lo reinicia una y otra vez, esperando cada vez más entre
intentos.

**Cómo se ve.** `kubectl get pods` muestra `CrashLoopBackOff` y un contador de `RESTARTS` que no para de subir.

**Qué evidencia aporta.** El estado del contenedor y sus reinicios, el último **código de salida con su significado**
(`1` = error de la aplicación, `137` = muerto con SIGKILL, `139` = fallo de segmentación, `143` = SIGTERM) y **las últimas líneas del log**
(con secretos enmascarados).

```text
── [HIGH] VD-K8S-001 · CrashLoopBackOff en el container app
Recurso: Pod venom-demo/crashloop
Causa probable: La aplicación terminó con un error (código 1). Las últimas líneas del log suelen indicar el motivo.
Evidencia:
  • pod-status: container "app" crash-looping, restartCount=4
  • exit-code: Last state: Terminated, exitCode=1, reason="Error"
  • log-tail: fatal: missing required env DB_HOST
```

**Confírmalo a mano.**

```bash
kubectl logs POD -n NS -c CONTENEDOR --previous    # el log de la última caída, no del arranque actual
kubectl describe pod POD -n NS                     # Last State, eventos, probes y variables de entorno
```

**Causas habituales.** Falta una variable de entorno o un secreto; no se alcanza la base de datos; el comando de arranque es incorrecto;
un error no controlado al iniciar; un archivo de configuración ausente.

**Cómo arreglarlo.** Lee la línea del log: casi siempre dice el motivo. Corrige la configuración, la imagen o el comando y vuelve a
desplegar. Si el código de salida es `137`, mira también [`VD-K8S-002`](#vd-k8s-002-oomkilled).

---

## VD-K8S-002: OOMKilled

**Qué significa.** El contenedor intentó usar más memoria que su `limits.memory` y el kernel lo terminó. Se reinicia y puede repetirse.

**Cómo se ve.** `kubectl describe pod` muestra `Last State: Terminated · Reason: OOMKilled · Exit Code: 137`.

**Qué evidencia aporta.** El límite actual, el uso observado (si tienes metrics-server) y **un valor nuevo sugerido**: 1,5× el
uso cuando está cerca del límite, o el doble del límite si no hay métricas. Es un punto de partida, no una medición.

```text
── [HIGH] VD-K8S-002 · OOMKilled en el container app
Recurso: Pod venom-demo/oom
Causa probable: El contenedor superó su límite de memoria de 32Mi y el kernel lo terminó (OOMKilled).
Evidencia:
  • last-state: container "app" Terminated reason=OOMKilled, exitCode=137, restartCount=4
  • limits: limits.memory=32Mi
  • memory-limit-change: container=app;from=32Mi;to=64Mi
Cómo arreglarlo (riesgo low): Subir limits.memory de 32Mi a 64Mi (punto de partida).
```

**Confírmalo a mano.**

```bash
kubectl describe pod POD -n NS | grep -A4 "Last State"
kubectl top pod POD -n NS --containers             # uso real de memoria (necesita metrics-server)
kubectl get pod POD -n NS -o jsonpath='{.spec.containers[*].resources}'
```

**Causas habituales.** Un límite demasiado ajustado; una fuga de memoria (el uso crece sin parar); una carga que ha crecido; un
`requests` muy por debajo del `limits`.

**Cómo arreglarlo.** Sube `resources.limits.memory`. **Este es el único hallazgo de Kubernetes que `venom fix` convierte en PR**: la evidencia
`memory-limit-change` indica el contenedor y los valores, y `pr-agent` los aplica en el Terraform que declara el workload.
Si el uso sigue creciendo tras subir el límite, busca una fuga: subir límites solo aplaza el problema.

---

## VD-K8S-003: ImagePullBackOff

**Qué significa.** Kubernetes no puede descargar la imagen del contenedor y reintenta con esperas crecientes.

**Cómo se ve.** `ErrImagePull` o `ImagePullBackOff` en `kubectl get pods`.

**Qué evidencia aporta.** **Distingue la causa**, que es lo que ahorra tiempo: imagen o *tag* inexistente, credenciales
ausentes o rechazadas, límite de descargas (*rate limit*) o un problema de red/DNS hacia el registro. A veces el registro responde algo
ambiguo (Docker Hub dice «no existe o requiere login» para ambos casos) y entonces lo indica tal cual.

```text
── [HIGH] VD-K8S-003 · No se puede descargar la imagen del container app
Recurso: Pod venom-demo/badimage
Causa probable: La imagen o el tag no existe en el registro (nombre o tag mal escrito, o la imagen nunca se publicó).
Evidencia:
  • pod-status: container "app" en ImagePullBackOff, image=busybox:this-tag-does-not-exist-venom
  • waiting-message: Back-off pulling image ...: rpc error: code = NotFound desc = failed to pull and unpack image ...
```

**Confírmalo a mano.**

```bash
kubectl describe pod POD -n NS | sed -n '/Events:/,$p'   # el mensaje exacto del registro
kubectl get pod POD -n NS -o jsonpath='{.spec.containers[*].image}'
```

**Causas habituales.** Un *tag* mal escrito o que nunca se publicó; un repositorio privado sin `imagePullSecrets`; en ECR, un rol del nodo sin
permiso de lectura; el límite de descargas anónimas de Docker Hub; un nodo sin salida a Internet.

**Cómo arreglarlo.** Según la causa: corrige la referencia, crea un `Secret` de tipo docker-registry y refiérelo en `imagePullSecrets`,
autentícate contra el registro o usa una caché espejo (pull-through cache en ECR).

---

## VD-K8S-004: Pending

**Qué significa.** El Pod existe, pero el *scheduler* no ha encontrado un nodo donde colocarlo.

**Cómo se ve.** El Pod se queda en `Pending` indefinidamente, sin contenedores en marcha.

**Qué evidencia aporta.** Lo que dice el scheduler (`FailedScheduling`) interpretado: **recursos insuficientes** (CPU o memoria), **taints** sin
toleration, **selectores** que no coinciden con ningún nodo, o un **PVC** sin enlazar o inexistente.

```text
── [HIGH] VD-K8S-004 · Pod en Pending: el scheduler no puede programarlo
Recurso: Pod venom-demo/pending
Causa probable: El scheduler no pudo asignar el Pod: el PersistentVolumeClaim "missing-claim" no existe.
Evidencia:
  • pod-condition: PodScheduled=False reason=Unschedulable
  • scheduler-message: 0/1 nodes are available: persistentvolumeclaim "missing-claim" not found. not found
  • pvc: PVC "missing-claim" no se pudo leer (¿no existe?)
```

**Confírmalo a mano.**

```bash
kubectl describe pod POD -n NS | grep -A6 FailedScheduling
kubectl get pvc -n NS                          # ¿existe y está Bound?
kubectl describe nodes | grep -E "Taints|Allocatable|Allocated"
```

**Causas habituales.** Las `requests` de CPU o memoria son mayores que lo libre en cualquier nodo; faltan nodos (o el autoscaler no escala);
un `nodeSelector` o afinidad demasiado estrictos; un PVC que no existe o no se puede enlazar (por ejemplo por zona).

**Cómo arreglarlo.** Reduce las `requests`, añade capacidad, ajusta selectores o tolerations, o crea el PVC que falta. Si usas Karpenter,
mira [`VD-K8S-008`](#vd-k8s-008-karpenter-sin-capacidad).

---

## VD-K8S-005: Probes fallando

**Qué significa.** Una *liveness*, *readiness* o *startup probe* falla. Una readiness fallida **saca el Pod del Service**; una liveness o startup
fallida **reinicia el contenedor**.

**Cómo se ve.** `READY 0/1` aunque el Pod esté `Running` (readiness), o reinicios con `Liveness probe failed` en los eventos.

**Qué evidencia aporta.** Lee los eventos `Unhealthy` y **solo los reporta si el estado actual los confirma** (readiness no lista, liveness con
reinicios, startup sin arrancar). Así no marca una aplicación que tardó en arrancar hace una hora. Distingue **conexión rechazada** (puerto incorrecto o la
app aún no escucha), **timeout**, **404** (ruta incorrecta), **5xx** (la app responde pero está mal) y **comando fallido**; e incluye la configuración de la
probe. Una liveness sube a `HIGH` desde 3 reinicios.

```text
── [HIGH] VD-K8S-005 · La probe de liveness falla en el container app
Recurso: Pod venom-demo/probe-liveness
Causa probable: El endpoint de la probe devuelve 404: la ruta configurada no existe en la aplicación. El kubelet reinicia el contenedor cada vez que la probe falla.
Evidencia:
  • pod-status: container "app": ready=true, restartCount=3
  • event: liveness probe failed (x4): HTTP probe failed with statuscode: 404
  • probe-config: liveness: httpGet /no-existe:8080, initialDelaySeconds=2, periodSeconds=3, timeoutSeconds=1, failureThreshold=1
```

**Confírmalo a mano.**

```bash
kubectl describe pod POD -n NS | grep -E "Liveness|Readiness|Startup|Unhealthy"
kubectl get endpoints SERVICIO -n NS            # ¿el Pod aparece como dirección lista?
kubectl get pod POD -n NS -o jsonpath='{.spec.containers[*].readinessProbe}'   # la probe tal como está configurada
```

**Causas habituales.** Puerto o ruta que no coinciden con la aplicación; una app lenta que necesita `startupProbe` o más `initialDelaySeconds`;
un `timeoutSeconds` de 1 s demasiado justo; una probe que comprueba dependencias externas, de modo que un fallo ajeno reinicia todos los Pods.

**Cómo arreglarlo.** Corrige puerto o ruta; añade una `startupProbe` para arranques lentos; sube `timeoutSeconds`; y separa liveness (¿vive el proceso?)
de readiness (¿puede atender tráfico?). Una liveness no debería depender de servicios externos.

---

## VD-K8S-006: Nodos NotReady

> **Solo con `-A`.** Los nodos son de todo el clúster, así que esta regla no corre con un `-n`.

**Qué significa.** Un nodo no está `Ready` (kubelet detenido, red de Pods caída, runtime de contenedores sin responder), o está `Ready` pero
con presión de memoria, disco o PIDs y a punto de desalojar Pods.

**Cómo se ve.** `kubectl get nodes` muestra `NotReady`; los Pods de ese nodo pasan a `Unknown` o se reprograman.

**Qué evidencia aporta.** La condición exacta del nodo, **desde cuándo** y **cuántos Pods se ven afectados**, y clasifica la causa: kubelet detenido (`Ready=Unknown`),
CNI sin inicializar (en EKS suele ser `aws-node`), PLEG poco sano, runtime caído o presión de recursos.

```text
── [HIGH] VD-K8S-006 · El nodo venom-worker no está Ready
Recurso: Node venom-worker
Causa probable: El nodo dejó de reportar su estado: el kubelet se detuvo o el nodo perdió conectividad con el control plane (instancia caída, kernel panic, red o seguridad).
Evidencia:
  • node-condition: Ready=Unknown, reason=NodeStatusUnknown, desde hace 0s
  • kubelet-message: Kubelet stopped posting node status.
  • pods-on-node: 3 Pods programado(s) en este nodo
```

**Confírmalo a mano.**

```bash
kubectl get nodes
kubectl describe node NODO | sed -n '/Conditions:/,/Addresses:/p'
kubectl get pods -A --field-selector spec.nodeName=NODO
```

**Causas habituales.** Instancia caída o con fallos de hardware; el kubelet se quedó sin memoria o disco; el CNI no arrancó (en EKS, `aws-node` o agotamiento de IPs
de la subred); partición de red entre el nodo y el control plane.

**Cómo arreglarlo.** Comprueba el estado de la instancia en EC2. Si es de un grupo gestionado, termina la instancia para que se reemplace: sus Pods se
reprograman en otros nodos. Si es recurrente, revisa security groups, rutas hacia el API server y el espacio en disco y memoria del nodo.

---

## VD-K8S-007: PDB que bloquea drains

**Qué significa.** Un *PodDisruptionBudget* no permite ninguna disrupción voluntaria, así que `kubectl drain` o una actualización de nodos se **quedará
colgada** esperando.

**Cómo se ve.** El drain muestra `Cannot evict pod as it would violate the pod's disruption budget` y no avanza. `kubectl get pdb` muestra
`ALLOWED DISRUPTIONS 0`.

**Qué evidencia aporta.** **Distingue cuatro causas**, porque cada una pide una acción distinta:

| Causa | Qué significa | Qué hacer |
|---|---|---|
| Sano pero sin margen | Los Pods están bien, pero `minAvailable` iguala a las réplicas (o `maxUnavailable: 0`, o una sola réplica) | Más réplicas o menos `minAvailable` |
| Pods no sanos | Hay menos Pods sanos de los que exige el presupuesto | **Arregla los Pods primero**; no relajes el PDB |
| Pide más de lo que existe | `minAvailable` mayor que las réplicas: imposible de cumplir | Corrige el número: es un error de configuración |
| Selector huérfano | No coincide con ningún Pod (severidad baja) | Elimínalo o corrige el selector |

```text
── [MEDIUM] VD-K8S-007 · El PDB pdb-greedy exige más Pods de los que existen
Recurso: PodDisruptionBudget venom-demo/pdb-greedy
Evidencia:
  • pdb-status: disruptionsAllowed=0, currentHealthy=1, desiredHealthy=3, expectedPods=1
  • pdb-spec: minAvailable=3, maxUnavailable=-
Cómo arreglarlo (riesgo medium): Ajustar el PDB o el workload para que el mantenimiento de nodos pueda avanzar.
  1. Bajar minAvailable por debajo del número de réplicas, o subir las réplicas del workload por encima de minAvailable.
  2. Preferir maxUnavailable: 1 (o un porcentaje) a un minAvailable fijo, para que el presupuesto escale con las réplicas.
```

**Confírmalo a mano.**

```bash
kubectl get pdb -A
kubectl describe pdb NOMBRE -n NS     # ¿cuántos Pods cubre y cuántos están sanos?
```

**Cómo arreglarlo.** Preferir `maxUnavailable: 1` (o un porcentaje) a un `minAvailable` fijo: el presupuesto escala con las réplicas. Un workload de una sola réplica con
`minAvailable: 1` bloquea siempre el drain. Avisa antes de que ocurra, y por eso es `MEDIUM` y no `HIGH`.

---

## VD-K8S-008: Karpenter sin capacidad

> **Solo con `-A`**, y **solo si Karpenter está instalado**: si no lo está, la regla no hace nada y no da error.

**Qué significa.** Karpenter, el autoscaler de nodos, no está consiguiendo capacidad para tus Pods: un `NodePool` que no está listo o que alcanzó su
límite, o un `NodeClaim` (la petición de un nodo) atascado antes de que el nodo sea utilizable.

**Cómo se ve.** Pods en `Pending` que no se resuelven; `NodeClaim` que no llegan a `Ready`.

**Qué evidencia aporta.** Para un **NodePool**: las condiciones que fallan (`Ready`, `NodeClassReady`, `ValidationSucceeded`, `NodeRegistrationHealthy`) y si llegó a
`spec.limits`. Para un **NodeClaim**: en qué etapa está atascado (`Launched` → `Registered` → `Initialized`) y desde cuándo. **El motivo y el mensaje se muestran
tal como los reporta Karpenter, sin interpretarlos**: la documentación de Karpenter no lista razones exactas y no inventamos ninguna.

```text
── [HIGH] VD-K8S-008 · El NodeClaim default-abcde lleva atascado en Launched     (ejemplo ilustrativo)
Recurso: NodeClaim default-abcde
Evidencia:
  • nodeclaim-condition: Launched=False, reason=<lo que reporte Karpenter>: <su mensaje>
  • nodeclaim-age: creado hace 3m0s y todavía sin pasar la etapa Launched
  • nodepool: default
```

**Confírmalo a mano.**

```bash
kubectl get nodepools,nodeclaims
kubectl describe nodeclaim NOMBRE
kubectl logs -n NAMESPACE-DE-KARPENTER deploy/karpenter | tail -50
```

**Cómo arreglarlo, según la etapa.** `Launched`: Karpenter no consigue crear la instancia: lee el motivo (capacidad, cuotas, permisos del rol del controlador) y amplía
tipos de instancia o zonas. `Registered`: la instancia existe pero no se une al clúster: revisa el rol IAM del nodo (access entries o `aws-auth`), security groups y el
arranque de la AMI. `Initialized`: se unió pero tiene taints de arranque o recursos sin registrar (CNI, device plugins). Si el NodePool llegó a su límite, súbelo o reduce la demanda.

---

## VD-K8S-009: IRSA mal cableado

**Qué significa.** Con *IRSA* (IAM Roles for Service Accounts) un Pod obtiene credenciales de AWS por su `ServiceAccount`: el ServiceAccount se anota con
`eks.amazonaws.com/role-arn` y un webhook de EKS inyecta `AWS_ROLE_ARN` y `AWS_WEB_IDENTITY_TOKEN_FILE` en los Pods **al crearlos**. Si algo de ese cableado falla, el Pod
no tiene el rol, y las llamadas a AWS fallan o, peor, **caen a las credenciales del nodo** sin que nadie lo note.

**Cómo se ve.** Errores `AccessDenied` o `NoCredentialProviders` en la aplicación; o peor, que funciona pero con los permisos del nodo.

**Qué evidencia aporta.** Tres comprobaciones, **un hallazgo por ServiceAccount** (un Deployment de 50 réplicas no genera 50 hallazgos):

- **ARN mal formado:** la anotación no tiene la forma `arn:…:iam::<12 dígitos>:role/<nombre>`.
- **Pods sin credenciales:** usan un ServiceAccount anotado pero no recibieron las variables. Suele ser porque se crearon antes de anotar, o porque el webhook no estaba disponible.
- **Rol desactualizado:** el `AWS_ROLE_ARN` del Pod no coincide con la anotación actual (cambió después de crear el Pod). Indica si difiere el nombre del rol o la cuenta.

Los IDs de cuenta de 12 dígitos salen **enmascarados** (`[ACCOUNT-ID]`).

```text
── [HIGH] VD-K8S-009 · Pods del ServiceAccount irsa-ok sin credenciales de IRSA
Recurso: ServiceAccount venom-demo/irsa-ok
Evidencia:
  • serviceaccount-annotation: eks.amazonaws.com/role-arn=arn:aws:iam::[ACCOUNT-ID]:role/demo-app
  • pods-not-injected: 2 Pods sin AWS_ROLE_ARN ni AWS_WEB_IDENTITY_TOKEN_FILE: irsa-web-7ff6fd6dc9-g8qj6, irsa-web-7ff6fd6dc9-k4j2s
Cómo arreglarlo (riesgo medium): Recrear los Pods para que el webhook inyecte las credenciales de IRSA.
```

**Confírmalo a mano.**

```bash
kubectl get serviceaccount NOMBRE -n NS -o jsonpath='{.metadata.annotations}'
kubectl describe pod POD -n NS | grep -E "AWS_ROLE_ARN|AWS_WEB_IDENTITY_TOKEN_FILE"
```

**Cómo arreglarlo.** Corrige el ARN con el valor exacto de IAM (`aws iam get-role --role-name NOMBRE`) y **recrea los Pods** (`kubectl rollout restart deployment/NOMBRE`): el webhook solo inyecta
el valor al crearlos.

**Lo que esta regla NO comprueba.** Mide la **consistencia dentro del clúster**, no los permisos: no mira la política de confianza del rol IAM, ni si existe el proveedor OIDC, ni qué puede hacer el rol. Que todo
esté bien cableado no garantiza que no haya `AccessDenied` por una política de confianza o de permisos incorrecta; eso se comprueba en IAM. Tampoco cubre *EKS Pod Identity* (decisión en el
[ADR 0008](https://github.com/CloudVipers/VenomOps/blob/main/docs/decisiones/0008-regla-irsa-venom-doctor.md)).
