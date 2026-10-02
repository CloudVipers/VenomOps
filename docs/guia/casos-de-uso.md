# Casos de uso

Situaciones habituales y el camino más corto en cada una. Todos los ejemplos de salida son reales.

## 1. «Un Pod no arranca y estoy de guardia»

**Situación.** Algo falla en producción y no sabes por dónde empezar: ¿es la imagen, la configuración, la memoria, el scheduler?

```bash
venom doctor -n pagos
```

Mira primero los `HIGH`. Cada hallazgo te dice **qué pasa** (la causa probable), **la prueba** (la evidencia: el código de salida, el último log, el mensaje del
scheduler) y **los pasos para arreglarlo**. Lo que habrías tardado en descubrir encadenando `get`, `describe`, `logs` y `events`, sale en una pantalla. Para confirmar
a mano cualquier hallazgo, el [catálogo de reglas](reglas.md) lista los comandos exactos.

**Qué te llevas.** El problema real en minutos y, sobre todo, **sin tocar el clúster**: solo lee.

## 2. «Voy a actualizar los nodos o hacer un drain»

**Situación.** Vas a rotar nodos (actualizar la AMI, el grupo de nodos, la versión de Kubernetes). Si hay un PodDisruptionBudget mal configurado, el `drain` se cuelga
a mitad y te quedas con medio clúster cordonado.

```bash
venom doctor -A        # -A para incluir también el estado de los nodos
```

Busca `VD-K8S-007` (PDB que bloquean drains) y `VD-K8S-006` (nodos que ya no están sanos). El primero es el que te cuenta, **antes** de empezar, cuál de tus
workloads va a impedir el mantenimiento y por qué: réplica única, `minAvailable` igual a las réplicas, Pods no sanos, o un presupuesto imposible de cumplir.

**Qué te llevas.** Una lista de comprobación previa al mantenimiento, en lugar de descubrirlo con el drain colgado.

## 3. «Un OOMKilled debería ser un Pull Request, no una tarde de trabajo»

**Situación.** Un servicio se queda sin memoria y la corrección es subir un límite en Terraform.

```bash
# 1. Del clúster al arreglo propuesto, sin tocar nada (--dry-run)
venom doctor -n pagos -o json | venom fix --finding - --supported --repo ./infra --dry-run
```

```text
--- a/main.tf
+++ b/main.tf
@@ -18,7 +18,7 @@
         limits = {
-          memory = "32Mi"
+          memory = "64Mi"
         }
```

```bash
# 2. Si el diff te convence, quita --dry-run: crea la rama, valida, hace el commit y abre el PR
export GITHUB_TOKEN=...
venom doctor -n pagos -o json | venom fix --finding - --supported --repo ./infra
```

El PR trae el hallazgo original, el cambio, el resultado de `terraform plan` y una lista de revisión para la persona que lo apruebe. **Nunca se mezcla solo.** El
valor nuevo es «un punto de partida»: ajústalo con el consumo real, y si la memoria crece sin parar, busca una fuga en lugar de seguir subiendo el límite.

## 4. «Quiero una segunda opinión sobre un plan de Terraform antes del `apply`»

**Situación.** Tienes un cambio de infraestructura y quieres ojos que revisen seguridad, costes, fiabilidad y operación a la vez, no solo el diff de código.

```bash
terraform plan -out plan.out && terraform show -json plan.out > plan.json
venom review --plan plan.json --model-id <modelo> --out ./informe
```

Cuatro especialistas analizan el plan **en paralelo**, se cuestionan entre sí en una segunda ronda y un moderador consolida. Lo que más valor tiene es lo que un linter no da:
**los desacuerdos explícitos**. Este es un fragmento de un informe real sobre un plan de red con tres NAT Gateways:

```text
## Resumen
- Hallazgos finales: 10 (7 high, 3 medium)
- Desacuerdos registrados: 8
- Riesgos aceptados: 2

### 1. Redundancia de Internet Gateway
- reliability: Reportó «falta de redundancia en IGW» (severity high)
- security:    Desacuerda: un único IGW es correcto; la redundancia está en NAT Gateways
- cost:        Desacuerda: no hay punto único de fallo; arquitectura estándar de AWS
- Resolución:  Resuelto
- Razón:       Un único IGW por región es la arquitectura estándar y correcta en AWS (…)
```

El informe no te dice «hazlo» o «no lo hagas»: te dice **qué se discutió y qué se decidió**, y te deja los riesgos aceptados por escrito. Antes de gastar nada, `--dry-run` muestra qué se
enviaría (ya sin secretos) y cuántos tokens costaría.

## 5. «Contrastar el plan con lo que pasa de verdad en producción»

**Situación.** El plan parece correcto sobre el papel, pero tu clúster ya muestra síntomas relacionados.

```bash
venom doctor -A -o json > hallazgos.json
venom review --plan plan.json --context-findings hallazgos.json --model-id <modelo> --out ./informe
```

El comité recibe los hallazgos del clúster **como contexto**: por ejemplo, un cambio de instancias que tocaría justo los nodos que ya están `NotReady`, o un plan que reduce réplicas de algo con un PDB
bloqueante.

## 6. «Vigilar el clúster desde CI o un cron»

**Situación.** Quieres que un trabajo programado avise cuando aparezcan hallazgos graves.

```bash
#!/usr/bin/env bash
set -euo pipefail
venom doctor -A -o json > hallazgos.json
graves=$(jq '[.[] | select(.severity=="critical" or .severity=="high")] | length' hallazgos.json)
echo "Hallazgos graves: $graves"
jq -r '.[] | select(.severity=="critical" or .severity=="high") | "\(.severity)\t\(.id)\t\(.resource.type)/\(.resource.name)\t\(.title)"' hallazgos.json
[ "$graves" -eq 0 ]        # falla (código 1) si hay graves: el trabajo de CI se marca en rojo
```

La salida JSON es un array de findings **validado contra el contrato** antes de escribirse; si algo lo incumpliera, el comando falla en vez de emitir un documento inválido. Los avisos
(reglas que no pudieron ejecutarse por falta de permisos) van a `stderr`, así que `stdout` es siempre JSON puro. Dale al trabajo una cuenta con el [`ClusterRole` de solo
lectura](uso.md#permisos-que-necesita-en-el-clúster).

## 7. «Auditar o migrar a IRSA»

**Situación.** Estás pasando cargas de las credenciales del nodo a roles por ServiceAccount (IRSA), o heredaste un clúster y no sabes cuántas están bien cableadas.

```bash
venom doctor -A -o json | jq '[.[] | select(.id=="VD-K8S-009")]'
```

`VD-K8S-009` lista, **un hallazgo por ServiceAccount**: ARN mal escritos, Pods creados antes de anotar (o con el webhook caído) que **no tienen el rol y pueden estar usando las credenciales del
nodo sin que nadie lo sepa**, y Pods con un rol viejo tras cambiar la anotación. Recuerda lo que no mide: no revisa las políticas de confianza ni los permisos de IAM.

## 8. «Karpenter no escala y los Pods siguen en Pending»

**Situación.** Hay Pods `Pending` y usas Karpenter.

```bash
venom doctor -A
```

Mira juntos `VD-K8S-004` (por qué el scheduler no los coloca) y `VD-K8S-008` (qué le pasa a Karpenter: un NodePool no listo, un límite alcanzado, o un NodeClaim atascado en `Launched`, `Registered` o
`Initialized`). La regla de Karpenter te muestra el motivo **exactamente como lo reporta Karpenter**, para que lo busques en sus logs sin una traducción intermedia.

## 9. «Dejar constancia de lo encontrado»

**Situación.** Necesitas adjuntar el diagnóstico a un ticket, un postmortem o una revisión de cambios.

```bash
venom doctor -n pagos -o json > hallazgo-2026-10-02.json
```

Cada hallazgo es autocontenido (recurso, evidencia, causa, pasos, referencias a documentación oficial y hora de detección), así que sirve como evidencia tal cual. Los secretos y los IDs de cuenta ya vienen
enmascarados.

---

## Lo que venom no es

- **No es un sistema de monitorización.** Toma una fotografía del momento en que lo ejecutas; no vigila ni alerta por sí solo (por eso el caso 6 usa un trabajo programado).
- **No sustituye la observabilidad.** Explica fallos de Kubernetes con la evidencia que el clúster conserva (estado, eventos, últimos logs); no ve métricas históricas ni trazas.
- **No arregla por su cuenta.** Los cambios los hace una persona, a través de un PR que se revisa. Es una decisión de diseño, no una carencia.
