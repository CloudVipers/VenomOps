# ADR 0008 — Regla de kdoctor para IRSA: qué se puede diagnosticar desde el clúster

- **Estado:** aceptado (alcance confirmado el 2026-10-01)
- **Fecha:** 2026-10-01
- **Alcance:** `packages/kdoctor`, regla `KD-K8S-009`. Cierra la última regla de la segunda tanda de `CLAUDE.md`
  («IRSA / EKS Pod Identity mal configurado»).

## Contexto

`kdoctor` es de **solo lectura sobre la API de Kubernetes** (regla 2 de `CLAUDE.md` y diseño de `ClusterReader`). La
documentación de AWS dice lo siguiente (comprobado en las páginas oficiales de EKS el 2026-10-01):

- **IRSA.** El ServiceAccount lleva la anotación `eks.amazonaws.com/role-arn`. El *Amazon EKS Pod Identity Webhook*
  vigila los Pods que usan ese ServiceAccount y les inyecta `AWS_ROLE_ARN` y `AWS_WEB_IDENTITY_TOKEN_FILE`
  (`/var/run/secrets/eks.amazonaws.com/serviceaccount/token`). La guía añade: «el ARN del rol debe coincidir con el
  ARN con que anotaste el ServiceAccount». El webhook actúa al **crear** el Pod, y también se pueden configurar esas
  variables a mano, así que su ausencia es una señal, no una prueba.
- **EKS Pod Identity.** Las asociaciones rol↔ServiceAccount viven en la **API de EKS**, no son objetos de Kubernetes.
  Cuando existe una, EKS añade al Pod `AWS_CONTAINER_CREDENTIALS_FULL_URI`, `AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE` y un
  volumen `eks-pod-identity-token`; un agente en cada nodo (DaemonSet) entrega las credenciales.

Lo que **no** se puede ver desde el clúster: la política de confianza del rol IAM, si existe el proveedor OIDC, si el
rol tiene permisos, ni si debería existir una asociación de Pod Identity que no existe.

## Decisión

1. **Primera versión: consistencia de IRSA dentro del clúster**, con tres comprobaciones que se apoyan solo en lo
   documentado:
   - **ARN mal formado:** la anotación `eks.amazonaws.com/role-arn` de un ServiceAccount no tiene la forma
     `arn:<partición>:iam::<12 dígitos>:role/<nombre>`.
   - **Pods sin inyectar:** hay Pods (no terminados) que usan un ServiceAccount anotado y no tienen `AWS_ROLE_ARN`
     ni `AWS_WEB_IDENTITY_TOKEN_FILE`. Causas probables: el Pod se creó antes de añadir la anotación, o el webhook no
     estaba disponible al crearlo. La solución habitual es recrear los Pods.
   - **ARN desactualizado:** el `AWS_ROLE_ARN` del Pod no coincide con la anotación actual del ServiceAccount (la
     anotación cambió después de crear el Pod).
2. **Un finding por ServiceAccount**, no por Pod: un Deployment de 50 réplicas no debe producir 50 findings iguales.
   La evidencia indica cuántos Pods están afectados y nombra algunos.
3. **Pod Identity queda fuera de esta versión.** Sin acceso a la API de EKS no se puede saber si falta una asociación, y
   comprobar el agente exige conocer el nombre exacto de su DaemonSet, que no está documentado en las páginas
   consultadas. Si se quiere cubrir, será un ADR aparte que decida si `kdoctor` puede llamar a `aws eks list-pod-identity-associations`
   (lectura permitida por la regla 2, pero deja de ser «solo Kubernetes») y se añadirá como opción explícita.
4. **Sin llamadas a AWS**: la regla solo lee ServiceAccounts y Pods. El ARN (que contiene el ID de cuenta) se
   enmascara con `redact` en la evidencia, como el resto de los datos que pueden ir a `--explain`.
5. Requiere un nuevo método de solo lectura `ListServiceAccounts` en `ClusterReader` (cubierto por el test de
   «solo get/list»).
6. **Alcance de namespace:** al ser objetos con namespace, la regla respeta `-n` (a diferencia de nodos y Karpenter).

## Qué no hace (y lo dice)

El hallazgo habla de **consistencia**, no de permisos: que todo esté bien cableado no significa que el rol IAM tenga la
política de confianza correcta. Los pasos sugeridos incluyen comprobar esa parte fuera del clúster (consola de IAM o
`aws iam get-role`), sin que `kdoctor` la ejecute.

## Consecuencias

- Cubre los fallos de IRSA que se ven en el clúster y más se repiten (anotar tarde, webhook caído, ARN con erratas).
- No promete detectar `AccessDenied` por una política de confianza o permisos incorrectos: eso queda para el usuario.
- Validación: en kind se puede reproducir «ServiceAccount anotado y Pods sin inyectar» (kind no tiene el webhook), así
  que esa comprobación sí se probará contra un API server real; la parte con el webhook activo no se puede probar sin EKS.

## Resultado de la validación (2026-10-01)

Probado en kind (sin el webhook de EKS) con un ServiceAccount anotado y tres réplicas sin inyectar, un ServiceAccount con
ARN `demo-app` y un Pod con un `AWS_ROLE_ARN` de otro rol: las tres comprobaciones salieron como se esperaba, un único
finding por ServiceAccount y sin ningún ID de cuenta en la salida. **No** se ha probado con el webhook activo en un EKS real.
