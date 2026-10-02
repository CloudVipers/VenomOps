# findings-schema

Contrato común de hallazgos (*findings*) de VenomOps: lo **producen** `venom-doctor` y otras fuentes, lo **corrige**
`pr-agent` y lo **debate** `arch-committee`. Decisiones y política de versionado en
[ADR 0001](../../docs/decisiones/0001-findings-schema.md).

## Contenido

```
schema/finding.schema.json   JSON Schema draft 2020-12 (fuente de verdad)
go/                          structs Go + validador + CLI findings-validate
python/                      modelos pydantic v2 + validador
examples/valid/              findings válidos
examples/invalid/            findings inválidos (uno por regla rota)
examples/expected-errors.json  violación esperada de cada ejemplo inválido: (ruta, keyword)
```

## Campos de un finding

| Campo | Tipo | Obligatorio |
|---|---|---|
| `id` | string (`VD-K8S-001`) | sí |
| `schema_version` | string (`1.x.y`) | sí |
| `source` | `venom-doctor` \| `pr-agent` \| `arch-committee` \| `manual` (`kdoctor`, el nombre anterior, solo se acepta al leer; ver [ADR 0009](../../docs/decisiones/0009-source-venom-doctor.md)) | sí |
| `severity` | `critical` \| `high` \| `medium` \| `low` \| `info` | sí |
| `title` | string | sí |
| `resource` | `{type, name, namespace?, region?, account_alias?, path?}` | sí |
| `evidence` | lista de `{kind, detail}` (al menos una) | sí |
| `root_cause` | string | sí |
| `suggested_fix` | `{summary, steps[], iac_hint?}` | sí |
| `risk_of_fix` | `low` \| `medium` \| `high` | sí |
| `references` | lista de URLs `http(s)` | no |
| `tags` | lista de strings únicos | no |
| `detected_at` | ISO 8601 con zona horaria | sí |

El contrato es cerrado: un campo desconocido es un error.

## Convenciones de `evidence`

Los datos accionables entre paquetes (p. ej. `memory-limit-change`, `required-tags`) viajan en `evidence` con un `kind` y un
formato `clave=valor;...` convenidos, sin modificar el schema: ver el [ADR 0004](../../docs/decisiones/0004-convenciones-evidence.md).

## Uso

Go:

```go
import findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"

f, err := findings.Parse(data) // valida contra el schema y decodifica; *ValidationError si es inválido
issues, _ := findings.Validate(data) // todas las violaciones: Path, Keyword, Message
```

Python:

```python
from findings_schema import Finding, validate

issues = validate(data)            # lista de ValidationIssue (vacía = válido)
finding = Finding.from_dict(data)  # valida y construye el modelo tipado
```

CLI (mismo veredicto que el validador Python, lo usa el test de paridad):

```bash
go run ./go/cmd/findings-validate examples/valid/*.json   # sale con 1 si algún archivo es inválido
```

## Desarrollo

```bash
make lint   # gofmt, go vet, ruff, mypy
make test   # go test -race + pytest (incluye el test de paridad Go/Python)
make build
```

Requiere Go 1.22+ y Python 3.12+ (el `Makefile` crea `python/.venv`).
