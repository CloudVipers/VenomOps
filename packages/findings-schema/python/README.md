# venomops-findings-schema (Python)

Tipos pydantic v2 y validador del contrato común de findings de VenomOps. Valida contra
`../schema/finding.schema.json`, la misma fuente que usa la versión Go.

```python
from findings_schema import Finding, validate

issues = validate(data)  # lista de ValidationIssue (vacía = válido)
finding = Finding.from_dict(data)  # valida contra el schema y construye el modelo tipado
```
