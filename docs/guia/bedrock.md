# Configurar Amazon Bedrock para usar la IA

Esta guía es el **paso a paso** para que `venom doctor --explain`, `venom fix --agent` y `venom review` puedan llamar a un modelo. Si todavía no sabes si te interesa la IA,
empieza por [la IA es opcional](ia.md): allí se explica qué aporta, qué datos salen y cuánto cuesta. **Nada de esto es necesario para diagnosticar un clúster.**

Hoy el único proveedor de modelos es **Amazon Bedrock, en tu propia cuenta de AWS**. Los datos no pasan por ningún servicio de CloudVipers.

## Qué necesitas al terminar

| Pieza | Para qué |
|---|---|
| Una **región** de AWS donde Bedrock ofrezca tu modelo | Dónde se hace la llamada |
| **Acceso al modelo** concedido en esa cuenta y región | Sin él, Bedrock responde `AccessDeniedException` |
| **Credenciales** de AWS en tu terminal | `venom` usa la cadena estándar de credenciales de AWS |
| **Permiso de IAM** `bedrock:InvokeModel` | Para invocar ese modelo y nada más |
| El **identificador del modelo** | No hay ninguno por defecto: lo indicas tú |

## Paso 1. Elige la región y el modelo

El modelo que hemos probado de verdad es **Claude Haiku 4.5**: `us.anthropic.claude-haiku-4-5-20251001-v1:0`. No hemos comparado otros, así que no podemos garantizarlos.

- El prefijo `us.` indica un **perfil de inferencia**: Bedrock reparte la llamada entre varias regiones de EE. UU. **La región que uses debe ser una de las que cubre el perfil** (por ejemplo `us-east-1` o `us-west-2`).
- Comprueba el identificador exacto que tu cuenta ofrece en tu región (los modelos se retiran y los identificadores cambian):

  ```bash
  aws bedrock list-inference-profiles --region us-east-1
  ```

- Elegir un modelo es una decisión de **coste y disponibilidad**; revisa la tarifa vigente de tu región antes de usarlo de forma habitual.

## Paso 2. Pide acceso al modelo

El acceso se concede **por cuenta de AWS y por región** (dos cuentas de la misma organización pueden tener acceso distinto).

1. Abre la consola de **Amazon Bedrock**, en la región elegida, y entra al **catálogo de modelos**.
2. Selecciona el modelo de **Anthropic** que vas a usar. La **primera vez**, Anthropic pide que envíes los *detalles de caso de uso* (una vez por cuenta, o una vez en la cuenta de administración de la organización). También
   se puede enviar con la API `PutUseCaseForModelAccess`.
3. Si el envío queda **pendiente**, las llamadas siguen fallando con `AccessDeniedException` aunque el formulario se haya enviado bien. Espera y vuelve a probar; el estado se consulta con la API
   `GetFoundationModelAvailability` (campo `agreementAvailability`: `AVAILABLE`, `PENDING`, `NOT_AVAILABLE` o `ERROR`).
4. La suscripción automática del modelo depende de **tus permisos de IAM**, incluidos los de AWS Marketplace. Si faltan, la primera llamada falla con `AccessDeniedException`.

El detalle y las excepciones están en la documentación de AWS: [Request access to models](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html).

## Paso 3. Configura las credenciales

`venom` no guarda ni pide credenciales: usa la **cadena estándar de AWS**. Sirve cualquiera de estas formas:

```bash
# a) Un perfil (también con IAM Identity Center / SSO)
aws sso login --profile mi-perfil          # solo si el perfil usa SSO
export AWS_PROFILE=mi-perfil

# b) Variables de entorno (por ejemplo, credenciales temporales)
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...

# c) Un rol (en una instancia EC2, un contenedor o un pipeline de CI): no hace falta exportar nada
```

Define también la **región**, igual que la del paso 1. Las funciones lo leen de formas distintas, así que lo más simple es exportarla siempre:

```bash
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
```

| Función | Cómo toma la región |
|---|---|
| `venom doctor --explain` | De la configuración de AWS (variables anteriores o la región del perfil). No tiene `--region` |
| `venom fix --agent`, `venom review` | Con `--region`; si no lo pasas, de la configuración de AWS |

Usa credenciales **de un usuario o rol con permisos mínimos** (paso 4) y evita las de un administrador.

## Paso 4. Da el permiso mínimo de IAM

Basta con `bedrock:InvokeModel` sobre el modelo que vas a usar. Con un perfil de inferencia (`us.…`) hace falta el permiso sobre **el perfil y sobre el modelo base**. El ejemplo de política, listo para
adaptar a tu cuenta y región, está en [la IA es opcional](ia.md#qué-necesitas-para-usarla). Pruébalo en tu cuenta: depende de tu configuración.

> Si tus políticas restringen `bedrock:InvokeModel` con claves de condición, la **primera** invocación también se comprueba contra ellas; si no se cumplen, falla con `AccessDeniedException` y la suscripción no se inicia.

## Paso 5. Indica el modelo

No hay modelo por defecto, a propósito. Puedes pasarlo en cada comando o definirlo una vez:

```bash
export VENOM_DOCTOR_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0   # venom doctor --explain
export PR_AGENT_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0       # venom fix --agent
export ARCH_COMMITTEE_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0 # venom review
```

Para dejarlo fijo, añade esas líneas a `~/.bashrc` o `~/.zshrc`. **Definir las variables no activa la IA por sí solo**: solo se usan cuando pides `--explain`, `--agent` o ejecutas `venom review`.

## Paso 6. Comprueba que funciona

Empieza por lo más barato.

**1) `venom doctor --explain`** (una llamada corta por hallazgo):

```bash
venom doctor -n mi-namespace --explain
```

Si todo está bien, cada hallazgo trae una sección `ai-explanation`. Si faltan credenciales, venom **avisa y sigue sin la explicación**:
`aviso: --explain requiere credenciales de AWS; se omite la explicación con IA.`

**2) `venom review`**, con una sola ronda para gastar menos:

```bash
terraform plan -out plan.out && terraform show -json plan.out > plan.json
venom review --plan plan.json --max-rounds 1 --out ./informe
```

`--dry-run` muestra lo que se enviaría y estima los tokens, pero **no llama a ningún modelo ni usa credenciales**: sirve para revisar el contenido, **no** para comprobar que Bedrock está bien configurado.
Para esa comprobación hay que hacer una llamada real, como las de arriba.

**3) `venom fix --agent`** solo si tienes un hallazgo sin arreglo automático (ver [Uso](uso.md#venom-fix-del-hallazgo-al-pull-request)); empieza siempre con `--dry-run`.

## Si algo falla

Cuando AWS rechaza la llamada, `venom` lo muestra como `Bedrock call failed: …` seguido del mensaje de AWS. Los casos más habituales:

| Mensaje | Qué pasa y qué hacer |
|---|---|
| `no AWS credentials available (configure AWS_PROFILE or environment variables)` | No se encontraron credenciales. Revisa el paso 3 (y haz `aws sso login` si tu perfil usa SSO) |
| `a model is required … there is no default` / `no Bedrock model configured` | Falta el modelo. Paso 5 |
| `AccessDeniedException` | Casi siempre es una de tres cosas: el **acceso al modelo** no está concedido o sigue pendiente (paso 2), tu **política de IAM** no permite `bedrock:InvokeModel` sobre ese perfil y modelo (paso 4), o la **región** no es la del acceso. Si el acceso figura como disponible y sigue fallando, AWS indica contactar a su soporte comercial |
| `ValidationException` que menciona que el modelo no admite *on-demand throughput* | Suele indicar que usaste el identificador del modelo sin el prefijo del perfil de inferencia. Usa el identificador `us.…` del paso 1 |
| Error de región o modelo no encontrado | El perfil `us.…` debe usarse en una región que cubra. Alinea `AWS_REGION` o `--region` |
| `ThrottlingException` | Demasiadas llamadas a la vez (el comité lanza varios agentes en paralelo). Espera y repite, o pide una cuota mayor |
| `the answer was cut off at max_tokens=…; raise --max-output-tokens` | La respuesta se truncó. Sube `--max-output-tokens` en `venom review` |

Más mensajes y soluciones en [Ayuda](ayuda.md#la-ia-no-funciona).

## Cómo comprobar que no se está usando IA

Ejecuta sin `--explain`, sin `--agent` y sin `venom review`: no hay ninguna llamada a un modelo. Para garantizarlo con permisos, niega `bedrock:InvokeModel` al rol con el que corre `venom`
([detalles](ia.md#cómo-asegurarte-de-que-no-se-usa-ia)).
