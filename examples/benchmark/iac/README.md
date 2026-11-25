# Benchmark de IaC etiquetado (experimento E3)

**55 casos de Terraform**: 42 con fallas sembradas y 13 limpios. Tienen **46 etiquetas** repartidas en las cuatro
especialidades del comité (seguridad, costo, confiabilidad y operaciones). Sirve para comparar la detección de
`arch-committee` con Checkov y Trivy (hipótesis H3 de la [propuesta](../../../docs/investigacion/propuesta.md)).

```
cases/<id>/main.tf       configuración del caso
cases/<id>/versions.tf   provider con credenciales ficticias (nunca se aplica)
cases/<id>/plan.json     terraform show -json del plan
labels.json              fallas sembradas por caso (recurso, categoría, severidad, descripción, CWE, alias)
build.py                 especificación de los casos: la única fuente que se edita
```

## Regenerar

```bash
python build.py            # fuentes y labels.json
python build.py --plans    # + plan.json de cada caso (terraform init -backend=false, plan -refresh=false, show -json)
python build.py --plans rds-combo clean-s3   # solo algunos casos
```

Solo se usan `init -backend=false`, `plan` y `show`, con credenciales de ejemplo de la documentación de AWS. Ningún
recurso se crea y ninguna API de AWS se consulta, porque todos los recursos son nuevos y el plan no refresca estado.
Los `plan.json` no contienen las credenciales; `research/eval/tests/test_benchmark.py` lo verifica.

## Qué es (y qué no es) una etiqueta

- Una etiqueta es una **falla sembrada a propósito**, no una auditoría exhaustiva. Un revisor puede reportar
  problemas reales que no están etiquetados; cuentan como falsos positivos y se discuten como amenaza a la validez
  (ver la [metodología](../../../docs/investigacion/metodologia.md)).
- `also` lista otras direcciones donde la misma falla se puede reportar legítimamente. Por ejemplo, una ACL pública
  puede atribuirse a `aws_s3_bucket_acl`, al bucket o a su bloqueo de acceso público.
- Los casos limpios están endurecidos y etiquetados (tags `Environment`, `Owner` y `Project`) para que la señal del
  benchmark venga de las fallas sembradas.
- La correspondencia con las reglas de Checkov y Trivy se registra al correr E3 contra sus salidas reales. No se
  escribió a mano, para no atribuirles reglas sin verificarlas.

| Categoría | Ejemplos de fallas |
|---|---|
| Seguridad | Buckets y temas SNS públicos, puertos abiertos a Internet, RDS público o sin cifrar, IAM `*:*`, `iam:PassRole` sobre `*`, confianza en cualquier principal, IMDSv1, secretos en `String` |
| Confiabilidad | RDS single-AZ, sin backups o sin protección contra borrado, DynamoDB sin PITR, ASG en una AZ, NAT único para tres AZ |
| Costo | gp2, io1 en dev, instancias y RDS sobredimensionados en dev, NAT por subred en dev, logs sin retención ni lifecycle, EIP sin usar |
| Operaciones | Recursos sin tags, VPC de producción sin flow logs |
