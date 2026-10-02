# La IA es opcional

**Lo esencial de VenomOps funciona sin ninguna IA y sin cuenta de AWS.** Diagnosticar un clúster, convertir un hallazgo en un Pull Request y actualizar `venom` son
funciones deterministas: dan el mismo resultado cada vez, no envían nada fuera de tu red (salvo hablar con la API de tu clúster) y no necesitan Amazon Bedrock.
La IA aparece solo en tres sitios, y solo si la pides.

## Qué usa IA y qué no

| Función | ¿Necesita IA? | Qué aporta la IA | Sin IA |
|---|---|---|---|
| `venom doctor` | **No** | n/a | Funciona completo: causa, evidencia y pasos para arreglarlo |
| `venom doctor --explain` | Sí, opcional | Una explicación ampliada, en prosa, de cada hallazgo | La salida normal ya trae causa probable, evidencia y pasos |
| `venom fix` (arreglos deterministas) | **No** | n/a | Arregla 4 tipos de hallazgo: OOMKilled, S3 sin cifrado, etiquetas obligatorias y gp2 a gp3 |
| `venom fix --agent` | Sí, opcional | Intenta arreglar hallazgos que no tienen arreglo determinista | Esos hallazgos se arreglan a mano siguiendo los pasos del hallazgo |
| `venom review` | **Sí** | Todo el valor del comité: varias perspectivas y sus desacuerdos | **No hay hoy un modo sin IA** |
| `venom update` | **No** | n/a | Funciona completo (solo necesita acceso a la web de VenomOps) |

Definir las variables de modelo (`VENOM_DOCTOR_BEDROCK_MODEL`, `PR_AGENT_BEDROCK_MODEL`, `ARCH_COMMITTEE_BEDROCK_MODEL`) **no activa nada por sí solo**: solo se usan
cuando pides la función con `--explain` o `--agent`, o ejecutas `venom review`. No hay ningún modelo por defecto.

## Qué aporta realmente

Conviene decirlo con claridad, para que decidas si te compensa:

- **`--explain`: valor modesto.** En nuestras pruebas reales con Amazon Bedrock la explicación fue correcta, pero en gran parte **reformula** lo que la regla ya te dice (la causa, la evidencia y
  los pasos). Sirve si prefieres un texto narrativo o para quien se incorpora al equipo; no descubre nada que la regla no hubiera dicho.
- **`--agent`: más alcance, más riesgo.** Es el último recurso para un hallazgo sin arreglo determinista. Un modelo puede equivocarse, así que está rodeado de salvaguardas (solo puede editar dentro del
  repositorio y con operaciones verificadas, el diff debe ser mínimo y pasar `terraform validate`, y el resultado es un PR que revisa una persona). Las pruebas reales nos obligaron a endurecerlo:
  se atascaba repitiendo una llamada fallida y no sabía cambiar números. Úsalo como borrador, nunca como decisión.
- **`venom review`: una segunda opinión, no un veredicto.** El comité no es determinista: dos ejecuciones pueden diferir. Su valor es la **amplitud** (seguridad, costes, fiabilidad y operación a la vez) y que los
  **desacuerdos quedan por escrito**. En un informe real el propio comité dejó un desacuerdo sin resolver y el informe lo dice (*«este informe es una ayuda a la decisión: lo debe revisar una persona»*). Léelo como una
  lista de cosas en las que pensar, no como una puerta de aprobación.

## Qué datos salen, función por función

Todo se enmascara antes de enviarse (claves de AWS, JWT, contraseñas y tokens en pares `clave=valor`, credenciales en URLs, llaves privadas e IDs de cuenta de 12 dígitos). Pero el enmascarado es **por patrones**: cubre lo habitual,
no puede garantizar que no quede información sensible de otro tipo (nombres internos de servicios, IPs, nombres de buckets). Por eso `--dry-run` muestra exactamente lo que se enviaría.

| Función | Qué se envía al modelo |
|---|---|
| `venom doctor --explain` | **Cada hallazgo completo**, en JSON y enmascarado: nombres de Pods, namespaces, imágenes, eventos y las últimas líneas de log |
| `venom review` | **El plan de Terraform**: direcciones y atributos de los recursos (los valores que Terraform marca como sensibles y los secretos, enmascarados), y los hallazgos previos si usas `--context-findings` (enmascarados y acotados) |
| `venom fix --agent` | El hallazgo, **el contenido de los archivos `.tf` que el agente lee** (enmascarado) y la salida de `terraform validate` y `plan` (enmascarada). No lee `terraform.tfstate` ni archivos `.tfvars` |

Los datos van a **tu** cuenta de AWS, con las credenciales que tú configures; no pasan por ningún servicio de CloudVipers.

> **Aviso para la versión 0.1.5 y anteriores.** `venom fix --agent` enviaba al modelo el contenido de los archivos `.tf` **sin enmascarar** (solo se enmascaraban el hallazgo y la salida de `terraform`). Lo encontramos al
> documentar esta página y ya está corregido en el código, con tests; saldrá en la siguiente versión. Hasta entonces, si usas `--agent`, evita secretos escritos en los `.tf`.

## Qué necesitas para usarla

- **Una cuenta de AWS con Amazon Bedrock**, el **acceso al modelo concedido en tu región** y credenciales (`AWS_PROFILE`, variables de entorno o un rol).
- **Un modelo que tú eliges**, con `--explain-model`, `--model-id` o la variable correspondiente. Es el único que hemos probado de verdad: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (Claude Haiku 4.5); no hemos comparado otros.
  Si usas un identificador que empieza por `us.` (un *perfil de inferencia*), la región debe coincidir.
- **Permisos de IAM mínimos.** Según la documentación de AWS, invocar un modelo mediante un perfil de inferencia exige permiso sobre el perfil **y** sobre el modelo base en cada región asociada.
  Este es un ejemplo (cambia la cuenta y la región; **pruébalo en tu cuenta**, porque depende de tu configuración):

  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": ["bedrock:InvokeModel"],
        "Resource": ["arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0"]
      },
      {
        "Effect": "Allow",
        "Action": ["bedrock:InvokeModel"],
        "Resource": ["arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"],
        "Condition": {"StringLike": {"bedrock:InferenceProfileArn": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0"}}
      }
    ]
  }
  ```

- **Mira la retención de datos del modelo antes de elegirlo.** Bedrock deja que controles si se retienen los prompts y las respuestas, y los proveedores de modelos no tienen acceso a ellos. Pero según su documentación,
  algunos modelos **más recientes** exigen, como condición de acceso, retención con revisión humana por parte de AWS (hasta 30 días). No afecta al modelo que recomendamos, pero revísalo si eliges otro.
- **Coste.** Se paga por tokens al precio de tu modelo y región (consulta la tarifa vigente). Como referencia, un informe real del comité sobre un plan de 20 recursos, con 2 rondas y Haiku 4.5, usó **59 325 tokens**
  (el tope por defecto es 200 000, y se cambia con `--max-tokens`). `venom review --dry-run` estima los tokens antes de gastar nada.

## Si no tienes acceso a IA

Es un caso normal y no pierdes lo principal:

- **Usa `venom doctor` y `venom fix`**: no necesitan IA. Funcionan incluso **sin salida a internet** (solo `venom update` necesita red): `venom doctor` solo habla con la API de tu clúster.
- Para los hallazgos sin arreglo automático, **sigue los pasos que trae el propio hallazgo**: están pensados para eso.
- **`venom review` no tiene hoy alternativa sin IA.** Si nunca vas a tener acceso, esa función no te sirve. No hay (todavía) soporte para otros proveedores de modelos: hoy solo Amazon Bedrock.

## Cómo asegurarte de que no se usa IA

- No pases `--explain` ni `--agent`, y no ejecutes `venom review`: sin eso **no hay ninguna llamada a un modelo**.
- Para garantizarlo con permisos, deniega `bedrock:InvokeModel` al rol o usuario con el que se ejecuta `venom` (por ejemplo, en CI). Las funciones con IA fallarán con un error claro y el resto seguirá funcionando.
- Si quieres auditar las invocaciones, Bedrock ofrece un registro de invocaciones de modelos (opcional) y CloudTrail registra la actividad de la API; comprueba qué eventos tienes activados en tu cuenta.

## Nuestra recomendación

Trata la IA como un **extra**, no como el producto. El valor diferencial de VenomOps (reglas que explican y verifican, arreglos mínimos y revisables, nada que se aplique solo) no depende de ella, y por eso no hace falta
pedir acceso a un modelo para empezar. Prueba primero `venom doctor`; si más adelante quieres una segunda opinión sobre un plan, activa `venom review` con un presupuesto de tokens acotado y revisa siempre el informe.
