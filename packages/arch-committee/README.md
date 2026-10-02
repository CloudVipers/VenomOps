# arch-committee

Un **comité virtual de agentes** revisa un plan de Terraform antes de que llegue a producción: cuatro especialistas
(**security**, **cost**, **reliability** y **operations**) lo analizan en paralelo, se cuestionan entre sí y un
**moderador** consolida, prioriza y registra los **desacuerdos** de forma explícita. No aplica ningún cambio.

Produce un `report.md` para personas y un `findings.json` válido contra [`findings-schema`](../findings-schema), que
puede alimentar a [`pr-agent`](../pr-agent).

## Flujo

```
terraform show -json plan.out ──▶ plan_parser (enmascara sensibles y secretos)
                                        │
        Ronda 1 (en paralelo):  security · cost · reliability · operations  ──▶ findings estructurados
                                        │
        Ronda 2 (réplica):      cada uno cuestiona hallazgos de los otros (agree / disagree / refine)
                                        │
        Moderador:              consolida duplicados, fija severidad, registra desacuerdos y riesgos aceptados
                                        │
                              report.md  +  findings.json (válido contra findings-schema)
```

Decisiones de diseño en el [ADR 0003](../../docs/decisiones/0003-orquestacion-arch-committee.md): orquestador propio
mínimo sobre la API Converse de Bedrock (sin Strands ni LangGraph; el flujo es fijo y lineal).

## Uso

```bash
make setup && . .venv/bin/activate

# 1. Genera el plan en JSON (solo lectura; nunca aplica nada)
terraform plan -out plan.out && terraform show -json plan.out > plan.json

# 2. Mira QUÉ se enviaría al modelo (ya redactado), sin llamarlo ni escribir nada
arch-committee review --plan plan.json --dry-run

# 3. Ejecuta el comité (el modelo es obligatorio: no hay valor por defecto)
export ARCH_COMMITTEE_BEDROCK_MODEL=<modelId>          # o --model-id
arch-committee review --plan ../../examples/plans/nat-per-subnet.json --out ./out
```

Opciones: `--max-tokens` (tope **total** de la ejecución, por defecto 200 000), `--max-output-tokens` (tope de salida de cada llamada,
8192), `--max-rounds` (1 = solo análisis, 2 = con réplica; por defecto 2), `--context-findings`, `--region`, `--dry-run`.

Salida en `--out`: `report.md` y `findings.json`.

### Findings previos como contexto

```bash
arch-committee review --plan plan.json --context-findings findings.json --out ./out   # repetible
```

Acepta uno o varios archivos con un finding o un array (p. ej. `venom-doctor -o json`). Se validan contra `findings-schema`, se
redactan y se limitan (50 findings, 10 000 caracteres, los más severos primero) antes de enviarse a la ronda 1 y al moderador,
siempre como **datos**. Sirve para contrastar el plan con lo observado en producción: por ejemplo, un OOMKilled que
`venom-doctor` vio en el Pod `oom` se enlaza con el recurso `kubernetes_pod_v1.oom` que el plan vuelve a declarar con un
límite bajo. El informe incluye una sección **Contexto previo**; `--dry-run` muestra exactamente qué se enviaría.

### Planes de ejemplo (`examples/plans/`)

Generados con `terraform plan` + `terraform show -json` a partir de [`examples/terraform/committee-*`](../../examples/terraform),
cada uno con fallas sembradas a propósito:

| Plan | Qué contiene |
|---|---|
| `public-bucket.json` | Bucket con política pública (`Principal: *`), bloqueo de acceso público desactivado y política IAM `*:*` |
| `rds-single-az.json` | RDS de producción en una sola AZ, sin backups ni cifrado, expuesta a Internet y sobredimensionada |
| `nat-per-subnet.json` | Un NAT Gateway por subred privada (3) en un entorno dev: tensión entre **costo** y **confiabilidad** |
| `k8s-oom.json` | Un `kubernetes_pod_v1` con `limits.memory = 32Mi`: se usa con los findings de `venom-doctor` como contexto (demo de la Fase 5) |

## Límites de costo y degradación

Cada llamada reserva tokens del presupuesto global antes de ejecutarse. Si se agota, o una llamada falla, el comité
**no se cae**: omite lo que no puede pagar, lo declara en los *Avisos* del informe y sigue. Garantías:

- Nunca se pierde un hallazgo en silencio (si el moderador omite uno, se conserva tal cual).
- Nunca se oculta un desacuerdo: si el moderador no se pronuncia sobre un `disagree`, queda como **sin resolver**.
- Si el moderador no puede ejecutarse, se hace una consolidación automática y los desacuerdos quedan **sin resolver
  (requieren decisión humana)**.
- Se descartan hallazgos sobre recursos que no existen en el plan (protección contra alucinaciones).

## Privacidad y seguridad

- **Redacción antes de enviar:** los valores que Terraform marca como sensibles (`after_sensitive`) se enmascaran, y
  además se redactan claves de AWS, JWT, contraseñas/tokens (por nombre de clave y en texto), credenciales en URLs,
  llaves privadas y IDs de cuenta de 12 dígitos. `--dry-run` muestra exactamente el payload resultante.
- **La llamada a un LLM es siempre explícita:** no hay modelo por defecto (decisión pendiente de `CLAUDE.md`, sección 11).
- El comité **solo lee** el plan: no ejecuta Terraform ni nada sobre la infraestructura.
- Los agentes responden mediante una herramienta de salida obligatoria (JSON con esquema), nunca texto libre, y **no
  tienen herramientas con efectos**. El contenido del plan se trata como datos: los prompts ordenan ignorar instrucciones
  incrustadas en tags o descripciones (inyección de prompt) y reportarlas como hallazgo.

## Ejemplo de informe (real)

Un informe completo generado con **Claude Haiku 4.5 en Amazon Bedrock** sobre el plan `nat-per-subnet` está en
[`docs/ejemplo-real-nat-per-subnet/`](docs/ejemplo-real-nat-per-subnet) (≈59 000 tokens, ~1 minuto). Incluye, entre otros,
el desacuerdo sembrado entre **cost** (1 NAT basta en dev) y **reliability** (un NAT por AZ evita el punto único de falla),
resuelto por el moderador como riesgo aceptado para un entorno dev. La calidad depende del modelo: revisa siempre el informe.

## Probado con un modelo real

Además de las pruebas con un LLM simulado, el comité se ejecutó contra Bedrock (Haiku 4.5) sobre los cuatro planes de
ejemplo. **Esa prueba destapó cuatro defectos que los dobles no podían mostrar**, ya corregidos:

| Hallazgo de la prueba real | Causa | Corrección |
|---|---|---|
| El moderador «no trataba» ningún hallazgo | Re-emitía el cuerpo completo de cada hallazgo y la respuesta se cortaba en `max_tokens`; Bedrock devuelve el `toolUse` **truncado** y `findings` (con valor por defecto) llegaba vacío | Una respuesta cortada es ahora un **error** (`LLMError`); el moderador **decide por referencia** (título, severidad, `merged_from`, decisión) y la evidencia/arreglo se adjuntan desde lo que reportó cada agente (prompt del moderador v2); tope de salida por llamada configurable (`--max-output-tokens`, 8192 por defecto); un reintento si aun así devuelve algo inutilizable |
| «Ruta privada sin NAT Gateway» (*critical*) **falsa** | `route { nat_gateway_id = … }` es desconocido hasta aplicar y el parser lo omitía dentro del bloque anidado, así que los agentes veían una ruta incompleta | Los valores configurados pero desconocidos se dejan **en su sitio** como `"(known after apply)"`, también dentro de bloques anidados; los prompts indican que eso no es «ausente» ni inseguro |
| Muchos desacuerdos «no resueltos» que no lo eran | El moderador fijaba la severidad final pero no rellenaba la lista de desacuerdos | Si el moderador dictaminó sobre el hallazgo en disputa, se registra como **resuelto** con su decisión; solo queda «sin resolver» lo que realmente omitió |
| Ids del moderador con formato imperfecto | p. ej. `"SEC-1, COST-1"` o `[sec-1]` | Extracción tolerante de ids |

Con el modelo real, sobre el plan de Kubernetes y la salida real de `venom-doctor` como contexto, el comité **enlazó el OOMKilled
observado en el clúster con el `kubernetes_pod_v1.oom` del plan** (severidad crítica), que es la integración buscada.

## Desarrollo

```bash
make lint    # ruff, ruff format --check, mypy --strict
make test    # pytest (LLM simulado: ninguna prueba llama a Bedrock)
```

Estructura: `plan_parser.py`, `redact.py`, `llm.py` (Converse + presupuesto), `models.py`, `agents/` (especialistas y
moderador; cada uno con su prompt versionado en `agents/prompts/<agente>.v1.md`), `orchestrator.py`, `report.py`, `cli.py`.

## Limitaciones conocidas

- **Un plan solo permite revisar lo que ya es conocido:** los atributos que dependen de valores que Terraform no sabe
  hasta aplicar (por ejemplo una política que usa el ARN de un bucket nuevo) no aparecen. Esos atributos se marcan como
  `known_after_apply` para que los agentes no los den por ausentes, pero su contenido no se puede revisar.
- **La calidad depende del modelo.** Probado manualmente con Haiku 4.5 en Bedrock (no en CI, donde se usa un LLM simulado). Un
  modelo real también exagera a veces (p. ej. severidades altas en un entorno dev) y repite hallazgos entre especialistas; el
  moderador los consolida, pero el informe es una ayuda a la decisión, no un veredicto.
- El texto de los informes está en español; el código y los identificadores, en inglés.
