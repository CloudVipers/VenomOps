# Seguridad

VenomOps está pensado para trabajar cerca de infraestructura real, así que sus límites **se hacen cumplir en el código**, no solo en las instrucciones ni en el
texto de un prompt. Esta página resume las garantías y dónde vive cada una.

## Lo que garantiza

| Garantía | Cómo se hace cumplir |
|---|---|
| **No modifica el clúster.** | El lector del clúster solo expone `get` y `list` (y leer logs). Hay un test que falla si algún lector emite otro verbo, y el [`ClusterRole`](uso.md#permisos-que-necesita-en-el-clúster) recomendado solo concede esos dos |
| **Nunca ejecuta `terraform apply` ni `destroy`.** | `venom fix` solo puede lanzar `terraform init`, `validate` y `plan`, mediante una lista blanca en el código. Cualquier otra orden lanza una excepción, aunque un modelo de IA se lo pidiera |
| **Nunca mezcla un PR ni empuja a `main`.** | El cliente de GitHub solo sabe abrir PRs. Trabaja en ramas `fix/…` y cada PR lleva una lista de revisión para una persona |
| **No toca tu repositorio sin avisar.** | Trabaja en una copia temporal; con `--dry-run` tu repositorio queda idéntico. Si el cambio toca archivos que no declaró, aborta |
| **La IA es opcional y explícita.** | Solo se usa con `--explain`, `--agent` o `venom review`, y siempre con un modelo que tú indicas: **no hay ninguno por defecto**. `--dry-run` te enseña lo que se enviaría |
| **Los secretos no salen.** | Antes de enviar nada a un modelo se enmascaran claves de AWS, JWT, contraseñas y tokens (`clave=valor`), credenciales en URLs, llaves privadas y IDs de cuenta de 12 dígitos. Lo mismo vale para los logs y eventos que aparecen en un hallazgo |
| **Un diff mínimo.** | Si un arreglo cambia más de lo estrictamente necesario, se aborta antes de crear el PR |

## Lo que sí envía a la IA (y cuándo)

Nada, por defecto. **`venom doctor` sin `--explain` no hace ninguna llamada de IA ni sale a ningún servicio externo.** Si activas la IA:

- `venom doctor --explain` envía **cada hallazgo** ya enmascarado a Amazon Bedrock en tu cuenta de AWS.
- `venom review` envía el **plan de Terraform** enmascarado (valores sensibles y secretos fuera) y, si lo pasas, los hallazgos previos.
- `venom fix --agent` envía el hallazgo y deja que el modelo use un conjunto reducido de herramientas (leer y editar archivos dentro del repositorio indicado, `terraform validate/plan`).

Los datos van a **tu** cuenta de AWS, con las credenciales que tú configures; no pasan por ningún servicio de CloudVipers. El enmascarado es por patrones y no puede garantizar que no quede información sensible de
otro tipo. Qué se envía exactamente en cada función, qué aporta de verdad y qué necesitas en AWS: [la IA es opcional](ia.md).

## Verificar lo que instalas

- **Paquetes `.rpm` y `.deb`:** el repositorio está firmado con GPG. En `dnf`, tanto los paquetes como los metadatos; en `apt`, el índice (`Release`), que
  contiene el hash de cada paquete, así que uno alterado no pasa la verificación. `dnf` muestra la huella de la clave la primera vez; debe ser
  `A7BE 1F5E 03EC 7AA9 C797 A9DE 3237 E8D7 9E6E 29C5` (`VenomOps Packages`). Con `gpgcheck=1` y `repo_gpgcheck=1` (como en la [instalación](instalacion.md)), un paquete o
  un índice manipulado se **rechaza**. Está probado: un cliente sin la clave, un `.rpm` alterado y un índice de `apt` alterado fallan por el motivo correcto.
- **Binarios sueltos:** cada release incluye `checksums.txt`; compruébalo con `sha256sum -c`.
- **Plugin de krew:** krew verifica el `sha256` del archivo antes de instalarlo.

## Permisos mínimos

- En Kubernetes: solo `get` y `list`. Si falta un permiso, la regla afectada se salta con un aviso claro y las demás siguen funcionando.
- En AWS: solo si usas la IA, acceso a invocar el modelo de Bedrock que elijas. Para abrir PRs, un `GITHUB_TOKEN` con permiso sobre el repositorio de Terraform.

## Las reglas, en una frase

Solo lectura y simulación sobre infraestructura real; nada de `apply`, `destroy` ni comandos de `kubectl` que modifiquen; sin secretos ni identificadores reales en el código ni en los registros;
la IA solo si la pides y sin secretos; y todo cambio pasa por una persona. Las reglas completas están en [`CLAUDE.md`](https://github.com/CloudVipers/VenomOps/blob/main/CLAUDE.md) y el
detalle de la arquitectura en [`docs/arquitectura.md`](https://github.com/CloudVipers/VenomOps/blob/main/docs/arquitectura.md).

## Reportar un problema de seguridad

Usa el [aviso de seguridad privado de GitHub](https://github.com/CloudVipers/VenomOps/security/advisories/new) del repositorio. No publiques los detalles de una vulnerabilidad en una incidencia abierta. Más
detalle en [`SECURITY.md`](https://github.com/CloudVipers/VenomOps/blob/main/SECURITY.md).
