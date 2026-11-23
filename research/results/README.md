# Resultados de los experimentos

Cada carpeta es **una corrida**: `<fecha UTC>-<experimento>[-etiqueta]/<experimento>.jsonl`, escrita por
[`venom-eval`](../eval). Los registros son *append-only* y llevan modelo, condición, repetición, versiones de los
prompts, temperatura, commit de git y la salida cruda necesaria para auditar cada caso.

Para reconstruir las tablas sin volver a llamar a ningún modelo:

```bash
cd research/eval && make setup
.venv/bin/venom-eval summarize ../results/<corrida>
```

| Corrida | Experimento | Modelo | Nota |
|---|---|---|---|
| `20261123-e4-baseline` | E4 (redacción) | n/a (determinista) | Línea base antes de mejorar los redactores: 7 de 21 secretos se filtran |

> Solo datos sintéticos: los casos salen de `examples/benchmark/` y ninguna corrida toca infraestructura real.
