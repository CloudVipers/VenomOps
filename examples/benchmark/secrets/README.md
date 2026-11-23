# Corpus de secretos sintéticos (experimento E4)

`corpus.json` mide la **confidencialidad** de VenomOps: cuántos secretos sobreviven a la redacción antes de llegar a un
modelo, a un PR o a un informe (hipótesis H4 de la [propuesta](../../../docs/investigacion/propuesta.md)). Todos los
valores son **falsos**.

El mismo corpus se prueba contra los tres redactores:

| Paquete | Prueba |
|---|---|
| `pr-agent` | `packages/pr-agent/tests/test_secrets_corpus.py` |
| `arch-committee` | `packages/arch-committee/tests/test_secrets_corpus.py` |
| `venom-doctor` | `packages/venom-doctor/internal/redact/corpus_test.go` |

## Formato

| Campo | Valores |
|---|---|
| `kind` | `secret` (debe desaparecer) o `benign` (debe quedar igual) |
| `expected` | `redacted`, `known_gap` (hoy se filtra), `unchanged` o `known_false_positive` (hoy se altera) |
| `secret` | La parte que no debe sobrevivir (solo en `secret`) |

Las brechas conocidas están **fijadas a propósito**: si un cambio cierra una, la prueba falla hasta que se actualiza el
corpus. Así la tasa de fuga medida nunca queda desactualizada.

## Línea base (versión 1.0.0)

| Medida | Valor |
|---|---|
| Secretos | 21 |
| Redactados | 14 (66,7 %) |
| Fugas (`known_gap`) | 7 (33,3 %) |
| Texto inofensivo alterado | 1 de 10 |

Fugas encontradas: pares clave-valor en **JSON** (`"password": "..."`), tokens de GitHub (`ghp_`) y de Slack (`xoxb-`),
secretos en prosa y datos de Secrets de Kubernetes cuya clave no parece sensible. El caso JSON es el más relevante: es el
formato de los planes de Terraform y de los findings.
