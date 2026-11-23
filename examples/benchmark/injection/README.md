# Corpus de inyección de prompt indirecta (experimentos E1 y E2)

`manifest.json` define los ataques contra los agentes de VenomOps como una **matriz**:

```
caso = payload (objetivo × técnica) × vector × artefacto base
```

Solo se combinan los payloads con los *targets* a los que apunta su objetivo. La versión 1.0.0 tiene **35 payloads** y
**145 casos**. Los casos no se guardan como copias: el arnés de [`research/eval/`](../../../research/eval) los materializa
de forma determinista (mismo manifest, mismos archivos), en un directorio temporal.

| Target | Objetivos del atacante | Vectores | Bases | Casos |
|---|---|---|---|---|
| `pr-agent` | ejecutar un comando, leer un secreto, escribir fuera del repo, agregar un bloque peligroso | comentario HCL, tag, descripción de variable, evidencia del finding | `s3-no-encryption` | 80 |
| `arch-committee` | suprimir un hallazgo, bajar su severidad | tag, descripción, finding de contexto | `public-bucket`, `rds-single-az` | 60 |
| `venom-doctor --explain` | inducir una recomendación peligrosa | log del Pod | `crashloop` | 5 |

**Técnicas:** instrucción directa, suplantación de autoridad, cambio de idioma (portugués), codificación en base64 e
instrucción partida en dos fragmentos.

Cada objetivo trae su **criterio de éxito** operativo (campo `success`), fijado antes de correr los experimentos.

## Seguridad

- Los payloads son sintéticos y solo apuntan a VenomOps; las URLs usan `attacker.example.com`.
- Ningún caso se ejecuta contra infraestructura real. En E1, la condición sin controles usa un ejecutor simulado que
  registra lo pedido y no ejecuta nada (ver la [metodología](../../../docs/investigacion/metodologia.md)).
- Las pruebas deterministas de `pr-agent` (`tests/test_injection.py`) ya cubren los objetivos de E1 con un modelo
  simulado que obedece la inyección.
