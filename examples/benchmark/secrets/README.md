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

## Resultados (intervalos de Wilson al 95 %)

| Corpus | Redactores | Secretos filtrados | Texto inofensivo alterado | Corrida |
|---|---|---|---|---|
| 1.0.0 (línea base) | antes de la corrección | 7/21 = 33,3 % [17,2 %, 54,6 %] | 1/10 | `research/results/20261123-e4-baseline` |
| 1.1.0 | con reglas para JSON, GitHub y Slack | 3/22 = 13,6 % [4,7 %, 33,3 %] | 1/12 | `research/results/20261123-e4-after-redaction-fix` |

La línea base encontró fugas en:

- pares clave-valor en **JSON** (`"password": "..."`, también escapados dentro de otro JSON);
- tokens de GitHub (`ghp_`) y de Slack (`xoxb-`);
- secretos en prosa;
- datos de Secrets de Kubernetes cuya clave no parece sensible.

El caso JSON era el más relevante, porque es el formato de los planes de Terraform y de los findings. Se corrigió en los
tres redactores. Siguen abiertos los secretos **sin formato** (prosa y claves de Kubernetes con nombres neutros). Ningún
patrón los reconoce, y eso es justamente lo que plantea la hipótesis H4.
