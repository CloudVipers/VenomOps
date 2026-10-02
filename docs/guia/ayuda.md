# Ayuda y solución de problemas

Cuando el resultado no es el esperado, empieza por aquí. Los mensajes están copiados tal cual salen.

## El diagnóstico

### «No se encontraron problemas», pero sé que algo falla

Las causas, de más a menos probable:

1. **Usaste `-n`.** Con un namespace no se revisan los nodos ni Karpenter (son de todo el clúster). `venom doctor` lo avisa por `stderr`:
   `nota: con un namespace (-n) no se revisan los nodos ni Karpenter; usa -A para diagnosticar todo el clúster.` Prueba con `-A`.
2. **Estás mirando el namespace equivocado.** Sin `-n`, usa el namespace del contexto actual, no «todos». Compruébalo con `kubectl config view --minify | grep namespace`, o usa `-A`.
3. **Un permiso faltante.** Una regla sin permiso se salta con un aviso (ver abajo). Pero **`events` y `pods/log` fallan en silencio**: son contexto opcional, así que si no los tienes el diagnóstico
   sigue, con menos evidencia. Usa el [`ClusterRole` de referencia](uso.md#permisos-que-necesita-en-el-clúster).
4. **El problema no es de lo que mide.** `venom doctor` cubre [nueve situaciones](reglas.md) de Kubernetes. Un fallo de la aplicación que no deja rastro en el estado de Kubernetes
   (una consulta lenta, un bug de lógica) no es de su ámbito: necesitas observabilidad.
5. **Ya pasó.** Los eventos de Kubernetes duran cerca de una hora y el estado de un Pod recuperado desaparece. Toma una fotografía del momento en que lo ejecutas.

### `aviso: rule VD-K8S-0xx: … is forbidden`

```text
aviso: rule VD-K8S-009: list serviceaccounts: serviceaccounts is forbidden: User "alguien" cannot list resource "serviceaccounts" in API group "" at the cluster scope
```

Tu usuario no puede leer ese recurso. Esa regla se salta; las demás siguen. Pide el [`ClusterRole` de solo lectura](uso.md#permisos-que-necesita-en-el-clúster) o quita la regla del alcance.
No es un fallo de la herramienta ni requiere permisos de escritura.

### Mi regla de nodos o de Karpenter no aparece

Solo corren con `-A`. La de Karpenter, además, **no hace nada si Karpenter no está instalado** (no da error) y necesita permiso de lectura sobre `nodepools` y `nodeclaims` de `karpenter.sh`.

### Un finding de PDB, probes o IRSA me parece un falso positivo

- **Probes (`VD-K8S-005`):** solo reporta si el estado **actual** del contenedor lo confirma, para no marcar arranques lentos ya resueltos. Si lo ves, la probe sigue fallando.
- **PDB (`VD-K8S-007`):** ignora los PDB que el controlador aún no ha procesado (acaban de crearse). Si lo ves, el estado está calculado.
- **IRSA (`VD-K8S-009`):** compara contra los **Pods vivos**. Si acabas de corregir la anotación, los Pods antiguos siguen con el valor anterior hasta que se recreen: `kubectl rollout restart`.

## `venom` y sus comandos

### `Error: kubectl-venom_doctor is not on PATH`

`venom doctor` ejecuta el binario `kubectl-venom_doctor`. Lo trae el paquete `venom`; si instalaste solo Python desde el código, instala el plugin (`kubectl krew install …` o el
[binario](instalacion.md#binario-suelto)). Comprueba con `which kubectl-venom_doctor`.

### `kubectl venom-doctor` no existe, pero el binario sí

`kubectl` encuentra los plugins por nombre de ejecutable en el `PATH`: debe llamarse **`kubectl-venom_doctor`** (con guion bajo) y ser ejecutable. Comprueba con `kubectl plugin list`.
Si lo instalaste con krew, añade `~/.krew/bin` al `PATH`.

### `venom fix`: `` `terraform` was not found on PATH; install it (terraform is needed to validate the fix) ``

`venom fix` valida siempre el arreglo con `terraform init` y `terraform validate` (incluso con `--dry-run`), así que necesita `terraform` instalado y no hay forma de saltarse la validación: es una garantía, no un
detalle. `--skip-plan` solo omite el `terraform plan`, que además necesita credenciales de AWS.

### `venom fix`: `there is no fixer for finding id '…'`

Ese hallazgo no tiene arreglo automático. Solo se corrigen [cuatro tipos](uso.md#qué-arregla-de-forma-automática). Para los demás puedes probar el agente opcional
con `--agent --model-id <modelo>`, o arreglarlo a mano siguiendo los pasos del propio hallazgo.

### `venom fix`: `no kubernetes_* workload in the Terraform matches pod …`

Para `VD-K8S-002` busca en tu Terraform el workload (`kubernetes_deployment`, `kubernetes_pod`…) con `metadata.name` y `namespace` **literales**. Si son variables (`var.nombre`) se
detiene en lugar de adivinar. Corrige el valor a mano o usa un `--repo` que apunte al módulo donde está declarado.

### `venom fix`: `the working tree has uncommitted changes; commit or stash them first`

Para abrir un PR necesita un árbol de trabajo limpio. Haz commit o `git stash`, o usa `--dry-run`, que no toca nada.

### `GITHUB_TOKEN is not set (needed to open the pull request)`

Exporta un token con permiso sobre el repositorio de Terraform (`export GITHUB_TOKEN=...`). No hace falta con `--dry-run`.

### La IA no funciona

| Mensaje | Qué pasa |
|---|---|
| `--agent needs an explicit model (--model-id or $PR_AGENT_BEDROCK_MODEL); there is no default` | Indica un modelo. No hay valor por defecto a propósito |
| `a model is required (--model-id or $ARCH_COMMITTEE_BEDROCK_MODEL); there is no default` | Igual, para `venom review` |
| `aviso: --explain requiere credenciales de AWS; se omite la explicación con IA.` | Configura credenciales (`AWS_PROFILE`, variables de entorno o un rol). El diagnóstico continúa sin la explicación |
| `no Bedrock model configured (use --explain-model or VENOM_DOCTOR_BEDROCK_MODEL)` | Para `--explain`, indica el modelo |
| `the answer was cut off at max_tokens=…; raise --max-output-tokens` | La respuesta del modelo se truncó; sube `--max-output-tokens` en `venom review` |

El modelo debe estar **habilitado en tu cuenta y región** de AWS. Si usas un perfil de inferencia (los identificadores que empiezan por `us.`), la región tiene que coincidir.

## Instalación

### `dnf` o `apt` rechazan el repositorio o la firma

- Comprueba que `gpgcheck=1` y `repo_gpgcheck=1` están como en la [instalación](instalacion.md) y que `gpgkey` apunta a la URL correcta.
- Si te pide confirmar la clave, la huella debe ser exactamente `A7BE 1F5E 03EC 7AA9 C797 A9DE 3237 E8D7 9E6E 29C5`. **Si es otra, no instales nada** y avísanos.
- En `apt`, asegúrate de que `/usr/share/keyrings/venom.gpg` existe y se descargó entero (`curl -fsSL`, sin errores).

### `krew install` falla con un error de `sha256`

El archivo descargado no coincide con el del manifiesto. No lo fuerces: es justo la protección. Vuelve a intentarlo (puede ser una descarga cortada) y, si persiste, abre una incidencia.

### Versión: ¿qué tengo instalado?

```bash
venom --version
kubectl-venom_doctor --version
```

## Si sigues atascado

Abre una [incidencia](https://github.com/CloudVipers/VenomOps/issues/new) con la versión (`venom --version`), el comando que ejecutaste y la salida (sin credenciales ni datos reales: el diagnóstico
ya enmascara secretos, pero revisa lo que pegas). Para un problema de seguridad, usa el [canal privado](seguridad.md#reportar-un-problema-de-seguridad).
