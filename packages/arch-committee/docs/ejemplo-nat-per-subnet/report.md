# Informe del comité de arquitectura

_Generado el 2026-09-30 19:42 UTC por **arch-committee** 0.1.0._

## Resumen

El comité consolidó 1 hallazgos.

- **Hallazgos finales:** 1 (1 medium)
- **Desacuerdos registrados:** 1
- **Riesgos aceptados:** 1

## Plan revisado

- Terraform 1.16.3; 20 recursos (20 create).
- Tipos: `aws_eip` ×3, `aws_internet_gateway` ×1, `aws_nat_gateway` ×3, `aws_route_table` ×3, `aws_route_table_association` ×3, `aws_subnet` ×6, `aws_vpc` ×1
- Los valores sensibles y los secretos se enmascararon **antes** de enviar el plan al modelo.

## Decisiones

| ID | Severidad | Recurso | Hallazgo | Origen | Decisión del moderador |
|---|---|---|---|---|---|
| AC-COST-001 | medium | `aws_nat_gateway.per_subnet[0]` | 3 NAT Gateways en un entorno dev | COST-1 | Se mantiene la severidad medium según el análisis de cost. |

## Desacuerdos explícitos

### 1. 3 NAT Gateways en un entorno dev

- **Hallazgos:** COST-1
- **cost:** Cada NAT tiene costo fijo mensual y por GB procesado.
- **reliability:** Colapsar a un solo NAT crea un punto único de falla: si cae su AZ, las otras dos pierden salida.
- **Resolución:** Riesgo aceptado
- **Razón:** En un entorno dev se acepta un único NAT; en producción se conservaría uno por AZ.

## Riesgos aceptados

- **COST-1:** Riesgo de disponibilidad aceptado en dev.

## Detalle de los hallazgos

### AC-COST-001 · MEDIUM · 3 NAT Gateways en un entorno dev

- **Recurso:** `aws_nat_gateway.per_subnet[0]`
- **Causa probable:** Cada NAT tiene costo fijo mensual y por GB procesado.
- **Evidencia:**
  - _topology_: 3 aws_nat_gateway, uno por subred privada
  - _debate_: reliability (disagree): Colapsar a un solo NAT crea un punto único de falla: si cae su AZ, las otras dos pierden salida.
  - _committee-decision_: Se mantiene la severidad medium según el análisis de cost.
- **Cómo arreglarlo** (riesgo low): Aplicar la corrección propuesta.
  1. Aplicar la corrección propuesta.

## Qué reportó cada especialista (ronda 1)

**security** — 0 hallazgos

**cost** — 1 hallazgos
- `COST-1` [medium] 3 NAT Gateways en un entorno dev (`aws_nat_gateway.per_subnet[0]`)

**reliability** — 0 hallazgos

**operations** — 0 hallazgos

### Réplicas (ronda 2)

| De | Sobre | Agente | Postura | Argumento |
|---|---|---|---|---|
| reliability | COST-1 | cost | disagree | Colapsar a un solo NAT crea un punto único de falla: si cae su AZ, las otras dos pierden salida. |

## Ejecución

- **Modelo:** `modelo-simulado (sin llamada real a Bedrock)`
- **Rondas ejecutadas:** 2; moderador: sí
- **Tokens:** 5,600 de un máximo de 200,000
- **Versiones de los prompts:** security v1, cost v1, reliability v1, operations v1, moderator v1

_Este informe es una ayuda a la decisión: lo debe revisar una persona. El comité no aplica ningún cambio._
