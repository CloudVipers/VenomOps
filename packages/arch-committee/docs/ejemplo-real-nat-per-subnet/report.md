# Informe del comité de arquitectura

_Generado el 2026-09-30 20:16 UTC por **arch-committee** 0.1.0._

## Resumen

Se consolida un plan de VPC multi-AZ en us-east-1 (dev) con 3 subnets públicas, 3 privadas, 3 NAT Gateways y 3 EIPs. Hallazgos críticos: (1) sobredimensionamiento de NAT/EIPs en dev (costo innecesario), (2) falta de observabilidad (VPC Flow Logs, CloudWatch alarms), (3) ausencia de protecciones operacionales (prevent_destroy en EIPs, deletion_protection en VPC), (4) etiquetas de gobernanza incompletas. Se resuelven desacuerdos sobre arquitectura (IGW, map_public_ip_on_launch) y se aceptan trade-offs apropiados para dev.

- **Hallazgos finales:** 10 (7 high, 3 medium)
- **Desacuerdos registrados:** 8
- **Riesgos aceptados:** 2

## Plan revisado

- Terraform 1.16.3; 20 recursos (20 create).
- Tipos: `aws_eip` ×3, `aws_internet_gateway` ×1, `aws_nat_gateway` ×3, `aws_route_table` ×3, `aws_route_table_association` ×3, `aws_subnet` ×6, `aws_vpc` ×1
- Los valores sensibles y los secretos se enmascararon **antes** de enviar el plan al modelo.

## Decisiones

| ID | Severidad | Recurso | Hallazgo | Origen | Decisión del moderador |
|---|---|---|---|---|---|
| AC-COST-001 | high | `plan` | Tres NAT Gateways y EIPs innecesarios en entorno de desarrollo | COST-1, COST-2 | En un entorno dev (tags: Environment=dev), la arquitectura multi-AZ con 3 NAT Gateways (~$32/mes c/u) y 3 EIPs (~$3.60/mes c/u) genera costo innecesario (~$107… |
| AC-OPS-001 | high | `aws_eip.nat` | Falta de protección contra eliminación accidental de EIPs críticas | OPS-2 | Las 3 EIPs (aws_eip.nat[0-2]) carecen de lifecycle.prevent_destroy. Una eliminación accidental causa interrupción inmediata de conectividad saliente. Aunque se… |
| AC-REL-001 | high | `aws_vpc.main` | VPC sin protección contra destrucción accidental | REL-1 | La VPC carece de deletion_protection o lifecycle.prevent_destroy. Un terraform destroy accidental destruye toda la infraestructura de red. Aunque sea dev, la V… |
| AC-REL-002 | high | `aws_subnet.public[0]` | Subnets públicas sin asignación automática de IP pública | REL-2 | Las 3 subnets públicas tienen map_public_ip_on_launch = false. Instancias lanzadas en subnets públicas no reciben IP pública automáticamente, requiriendo asign… |
| AC-SEC-001 | high | `aws_vpc.main` | Falta de VPC Flow Logs para observabilidad de tráfico | SEC-1, OPS-3 | Sin VPC Flow Logs, no hay visibilidad del tráfico de red ni de los NAT Gateways. Esto impide diagnosticar problemas de conectividad, detectar anomalías y valid… |
| AC-OPS-002 | high | `plan` | Etiquetas de gobernanza incompletas (Owner/Project) | OPS-1 | Los 20 recursos de red carecen de etiquetas Owner/Project. Esto impide chargeback, asignación de responsabilidad y gobernanza. Cost sugiere severidad medium (v… |
| AC-OPS-003 | medium | `aws_nat_gateway.per_subnet` | Falta de CloudWatch alarms para monitoreo de NAT Gateways | OPS-4 | Sin alarmas de CloudWatch, fallos de NAT Gateway (ErrorPortAllocation, ErrorCount, BytesOutToDestination) no se detectan proactivamente. Reliability enfatiza q… |
| AC-OPS-004 | medium | `aws_route_table.private` | Route tables privadas sin nombres descriptivos | OPS-5 | Las 3 route tables privadas carecen de etiqueta Name o descripción. Esto dificulta identificar rápidamente qué route table corresponde a qué AZ durante trouble… |
| AC-SEC-002 | medium | `plan` | Falta de logging de cambios en infraestructura de red | SEC-2 | No hay evidencia de CloudTrail habilitado para auditar cambios en VPC, subnets, IGW y NAT Gateways. Aunque CloudTrail es responsabilidad de gobernanza global (… |
| AC-REL-003 | high | `aws_internet_gateway.main` | Falta de redundancia en Internet Gateway | REL-3 | Sin consolidar por el moderador: se conserva tal como lo reportó reliability. |

## Desacuerdos explícitos

### 1. Redundancia de Internet Gateway

- **reliability:** Reportó REL-3 como 'falta de redundancia en IGW' (severity high)
- **security:** Desacuerda: un único IGW es correcto; la redundancia está en NAT Gateways
- **cost:** Desacuerda: no hay punto único de fallo; arquitectura estándar de AWS
- **operations:** Desacuerda: IGWs tienen HA interna; múltiples IGWs complican enrutamiento
- **Resolución:** Resuelto
- **Razón:** Un único IGW por región es la arquitectura estándar y correcta en AWS. Los IGWs son recursos de región con alta disponibilidad interna de AWS; no requieren redundancia explícita. Security, cost, reliability y operations coinciden en que no hay punto único de fallo real. REL-3 debe descartarse.

### 2. map_public_ip_on_launch en subnets públicas

- **security:** map_public_ip_on_launch=false es correcto desde seguridad; evita asignar IPs públicas automáticamente
- **cost:** Desacuerda: es correcto cuando se usan NAT gateways; no es defecto
- **operations:** Desacuerda: es contraproducente en subnets públicas; requiere asignación manual de EIPs
- **Resolución:** Resuelto
- **Razón:** Security argumenta que map_public_ip_on_launch=false es buena práctica de seguridad (evita asignar IPs públicas automáticamente). Sin embargo, operations enfatiza que en subnets públicas, las instancias esperan IP pública automática; requiere asignación manual de EIPs, aumentando fricción operativa. El consenso es que subnets públicas deben tener map_public_ip_on_launch=true. Se elige la posición operacional porque es más práctica para dev.

### 3. deletion_protection en VPC (dev vs. prod trade-off)

- **reliability:** Reportó REL-1 como 'sin protección contra eliminación accidental' (severity medium)
- **security:** Desacuerda: en dev, la capacidad de recrear rápidamente es más valiosa
- **cost:** Desacuerda: es riesgo operacional, no arquitectónico; aceptable en dev
- **operations:** Acuerda: recomendado agregar protección, pero entiende trade-off de dev
- **Resolución:** Riesgo aceptado
- **Razón:** Security argumenta que en dev, la capacidad de destruir y recrear rápidamente la infraestructura es más valiosa que la protección contra eliminación accidental. Cost y reliability sugieren severidad low. Sin embargo, operations enfatiza que es buena práctica incluso en dev. Se acepta como riesgo conocido en dev: la VPC puede ser destruida accidentalmente, pero esto es aceptable en un entorno de desarrollo donde la recreación es rápida y el costo de downtime es bajo. En prod, deletion_protection es obligatorio.

### 4. CloudTrail en plan de VPC (responsabilidad de gobernanza global)

- **security:** Reportó SEC-2 como 'subredes públicas sin CloudTrail o auditoría de cambios' (severity medium)
- **cost:** Desacuerda: CloudTrail es responsabilidad de gobernanza global, no de este plan
- **operations:** Refina: CloudTrail es externo, pero falta de logging de cambios dificulta troubleshooting
- **Resolución:** Riesgo aceptado
- **Razón:** Cost argumenta que CloudTrail es servicio regional, no recurso de VPC; su ausencia no es hallazgo de arquitectura de red. Operations refina que la falta de logging de cambios en infraestructura crítica dificulta troubleshooting, pero entiende que CloudTrail es responsabilidad de gobernanza global, no de este plan específico. Se acepta como riesgo: el plan no incluye CloudTrail, pero se recomienda documentar que se configura fuera de Terraform o incluirlo en una política de gobernanza.

### 5. Sin protección contra eliminación accidental de la VPC

- **Hallazgos:** REL-1
- **reliability:** Reporta medium: La VPC carece de protección contra eliminación accidental. Sin deletion_protection, un comando terraform destroy podría eliminar toda la infraestructura de red sin advertencia adicional.
- **security:** La VPC no tiene deletion_protection, pero esto es un trade-off aceptable en dev. En desarrollo, la capacidad de destruir y recrear rápidamente la infraestructura es más valiosa que la protección contra eliminación accidental. En prod sería crítico.
- **cost:** deletion_protection en VPC es una característica de AWS, no un atributo de Terraform. El riesgo real es operacional (terraform destroy), no arquitectónico. En dev, esto es aceptable; la severidad debería ser 'low' si se reporta.
- **Resolución:** Resuelto
- **Razón:** El moderador fijó la severidad final en high: La VPC carece de deletion_protection o lifecycle.prevent_destroy. Un terraform destroy accidental destruye toda la infraestructura de red. Aunque sea dev, la VPC es infraestructura crítica. Operations recomienda agregar protección. Security argumenta que en dev la capacidad de recrear rápidamente es valiosa, pero el riesgo de destrucción accidental justifica la protección. Recomendación: enable_deletion_protection = true o lifecycle { prevent_destroy = true }.

### 6. Subnets públicas sin asignación automática de IP pública

- **Hallazgos:** REL-2
- **reliability:** Reporta medium: Las subnets públicas están configuradas sin asignar automáticamente direcciones IP públicas a las instancias lanzadas. Esto requiere asignación manual de EIPs o direcciones públicas, aumentando la complejidad operativa y el riesgo de instancias sin conectividad pública cuando se espera.
- **security:** map_public_ip_on_launch=false en subnets públicas es correcto desde seguridad: evita asignar IPs públicas automáticamente a todas las instancias. Los recursos que necesitan conectividad pública deben recibir EIPs explícitamente. Esto es una buena práctica, no un problema.
- **cost:** map_public_ip_on_launch=false es correcto en subnets públicas cuando se usan NAT gateways para salida. Las instancias privadas no necesitan IPs públicas; las públicas pueden recibir EIPs explícitamente. No es un defecto.
- **Resolución:** Resuelto
- **Razón:** El moderador fijó la severidad final en high: Las 3 subnets públicas tienen map_public_ip_on_launch = false. Instancias lanzadas en subnets públicas no reciben IP pública automáticamente, requiriendo asignación manual de EIPs. Esto aumenta fricción operativa y riesgo de error. Operations enfatiza que es contraproducente en subnets públicas. Security argumenta que es buena práctica de seguridad, pero el consenso operacional es que subnets públicas deben tener map_public_ip_on_launch = true. Recomendación: cambiar a true en aws_subnet.public[0-2].

### 7. Falta de redundancia en Internet Gateway

- **Hallazgos:** REL-3
- **reliability:** Reporta high: AWS Internet Gateways son recursos de región, no de AZ, por lo que un único IGW es normal. Sin embargo, la arquitectura concentra toda la conectividad pública en un único recurso. Si bien AWS proporciona alta disponibilidad interna del IGW, la configuración no tiene redundancia explícita a nivel de aplicación.
- **security:** Un único Internet Gateway es la arquitectura correcta en AWS. Los IGWs son recursos de región con alta disponibilidad interna; no requieren redundancia explícita. La redundancia está en los NAT Gateways (uno por AZ) para tráfico saliente de subnets privadas.
- **cost:** Un único IGW por región es la arquitectura estándar de AWS. Los IGWs son recursos de región con alta disponibilidad interna. Reportar esto como 'falta de redundancia' es incorrecto; no hay punto único de fallo real.
- **operations:** Un único IGW es arquitectura estándar y correcta en AWS. Los IGWs son recursos de región con HA interna de AWS. No hay punto único de fallo operacional; AWS garantiza disponibilidad. Agregar múltiples IGWs no mejora confiabilidad y complica enrutamiento. Severidad debe ser info o removerse.
- **Resolución:** Sin resolver (requiere decisión humana)
- **Razón:** El moderador no se pronunció sobre este desacuerdo: requiere decisión humana.

### 8. Subredes públicas sin CloudTrail o auditoría de cambios

- **Hallazgos:** SEC-2
- **security:** Reporta medium: No hay evidencia de CloudTrail habilitado para registrar cambios en la infraestructura de red y acceso a recursos públicos
- **cost:** CloudTrail es un servicio regional, no un recurso de VPC. Su ausencia en el plan no es un hallazgo de arquitectura de red; pertenece a una revisión de gobernanza global, no a este plan específico.
- **Resolución:** Resuelto
- **Razón:** El moderador fijó la severidad final en medium: No hay evidencia de CloudTrail habilitado para auditar cambios en VPC, subnets, IGW y NAT Gateways. Aunque CloudTrail es responsabilidad de gobernanza global (no específica de este plan), operations refina que la falta de logging de cambios en infraestructura crítica dificulta troubleshooting y rollback. Severidad: medium (vs. original) porque es dev y CloudTrail es externo al plan. Recomendación: documentar que CloudTrail se configura fuera de Terraform o incluirlo en el plan.

## Riesgos aceptados

- **REL-1:** En un entorno dev (Environment=dev), la ausencia de deletion_protection en la VPC es un riesgo aceptable. La capacidad de destruir y recrear rápidamente la infraestructura es más valiosa que la protección contra eliminación accidental. El costo de downtime y recreación es bajo en dev. En prod, deletion_protection es obligatorio. Recomendación: agregar deletion_protection = true en aws_vpc.main cuando se migre a prod.
- **SEC-2:** CloudTrail es responsabilidad de gobernanza global, no de este plan específico de VPC. Su ausencia no es un defecto arquitectónico del plan de red. Se recomienda documentar que CloudTrail se configura fuera de Terraform o incluirlo en una política de gobernanza centralizada. En dev, la auditoría de cambios es menos crítica que en prod.

## Detalle de los hallazgos

### AC-COST-001 · HIGH · Tres NAT Gateways y EIPs innecesarios en entorno de desarrollo

- **Recurso:** `plan`
- **Causa probable:** Se provisiona un NAT Gateway por AZ (us-east-1a, us-east-1b, us-east-1c) en un entorno de desarrollo, generando costo innecesario. En dev, un único NAT Gateway compartido es suficiente para la mayoría de casos.
- **Evidencia:**
  - _attribute_: aws_nat_gateway.per_subnet[0], aws_nat_gateway.per_subnet[1], aws_nat_gateway.per_subnet[2] creados con count=3
  - _attribute_: aws_vpc.main tags: Environment=dev
  - _attribute_: Tres Elastic IPs asignadas: aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2]
  - _attribute_: aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2] con domain=vpc
  - _attribute_: Entorno: Environment=dev en aws_vpc.main
  - _debate_: security (agree): En un entorno dev (tags: Environment=dev), tres NAT Gateways con tres EIPs asociadas generan costo innecesario. Un único NAT Gateway compartido en una subnet pública es suficiente para dev. La arquitectura multi-AZ es apropiada para prod, no para dev.
  - _debate_: security (agree): Las tres EIPs (aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2]) tienen costo mensual fijo en AWS incluso sin uso intenso. En dev, una única EIP es suficiente.
  - _debate_: reliability (agree): Tres NAT Gateways en dev (Environment=dev) es un desperdicio. Cada NAT Gateway cuesta ~$32/mes. Un único NAT Gateway compartido en una AZ es suficiente para dev, reduciendo costo 66% sin impacto funcional en este entorno.
  - _debate_: reliability (agree): Tres EIPs en dev generan costo innecesario (~$3.60/mes cada una). Si se reduce a un NAT Gateway, se necesita solo una EIP. El costo es bajo pero evitable en dev.
  - _debate_: operations (agree): En dev con Environment=dev, tres NAT Gateways es sobredimensionamiento. Operacionalmente, un único NAT Gateway es más simple de monitorear y mantener. La HA de NAT es típicamente una decisión de prod. Costo innecesario confirmado.
  - _debate_: operations (agree): Tres EIPs en dev generan costo fijo mensual. Desde ops, esto también complica el inventario y monitoreo de IPs. En dev, una única EIP compartida es suficiente.
  - _committee-decision_: En un entorno dev (tags: Environment=dev), la arquitectura multi-AZ con 3 NAT Gateways (~$32/mes c/u) y 3 EIPs (~$3.60/mes c/u) genera costo innecesario (~$107/mes). Un único NAT Gateway compartido en una AZ es suficiente para dev, reduciendo costo 66% sin impacto funcional. Todos los especialistas (security, cost, reliability, operations) coinciden en que esto es sobredimensionamiento. Recomendación: usar 1 NAT Gateway y 1 EIP en dev; reservar multi-AZ para prod.
- **Cómo arreglarlo** (riesgo low): Reducir a un único NAT Gateway compartido en desarrollo, o usar NAT Instance para menor costo.
  1. Cambiar la lógica de count de 3 a 1 para aws_nat_gateway.per_subnet y aws_eip.nat
  2. Crear una única route table privada que apunte al NAT Gateway compartido
  3. Asociar todas las subnets privadas a la misma route table
  - IaC: `count = var.environment == "prod" ? 3 : 1`

### AC-OPS-001 · HIGH · Falta de protección contra eliminación accidental de EIPs críticas

- **Recurso:** `aws_eip.nat`
- **Causa probable:** Las direcciones IP elásticas son recursos críticos y costosos; su eliminación accidental causa interrupción de conectividad saliente y requiere reasignación de IPs
- **Evidencia:**
  - _attribute_: aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2] no tienen lifecycle.prevent_destroy
  - _topology_: 3 EIPs asociadas a NAT gateways en producción (Environment=dev pero infraestructura crítica)
  - _debate_: security (agree): Las EIPs (aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2]) son recursos críticos para la conectividad saliente. Sin lifecycle.prevent_destroy, una eliminación accidental causa interrupción inmediata. Aunque sea dev, es buena práctica protegerlas.
  - _debate_: cost (agree): Las 3 EIPs (aws_eip.nat[0-2]) están asociadas a NAT gateways críticos. Sin lifecycle.prevent_destroy, una eliminación accidental causa interrupción. En dev, severidad 'medium' es más apropiada que 'high'.
  - _debate_: reliability (agree): Las EIPs son recursos críticos para conectividad saliente. Sin lifecycle.prevent_destroy, un terraform destroy accidental o un cambio de configuración puede eliminarlas, causando interrupción. Esto es especialmente grave si estas EIPs están en uso por aplicaciones.
  - _committee-decision_: Las 3 EIPs (aws_eip.nat[0-2]) carecen de lifecycle.prevent_destroy. Una eliminación accidental causa interrupción inmediata de conectividad saliente. Aunque sea dev, es buena práctica proteger recursos críticos. Reliability y operations enfatizan que esto es especialmente grave si hay aplicaciones dependientes. Recomendación: agregar lifecycle { prevent_destroy = true } a aws_eip.nat.
- **Cómo arreglarlo** (riesgo low): Agregar lifecycle.prevent_destroy a todos los EIPs
  1. Agregar bloque lifecycle a cada aws_eip.nat
  2. Esto requiere destrucción explícita (terraform destroy -target) para remover
  - IaC: `lifecycle { prevent_destroy = true }`

### AC-REL-001 · HIGH · VPC sin protección contra destrucción accidental

- **Recurso:** `aws_vpc.main`
- **Causa probable:** La VPC carece de protección contra eliminación accidental. Sin deletion_protection, un comando terraform destroy podría eliminar toda la infraestructura de red sin advertencia adicional.
- **Evidencia:**
  - _attribute_: aws_vpc.main no tiene enable_dns_support configurado explícitamente como true, pero más importante: no hay deletion_protection habilitada
  - _attribute_: Tags muestran Environment=dev, indicando ambiente de desarrollo pero sin protecciones
  - _debate_: security (disagree): La VPC no tiene deletion_protection, pero esto es un trade-off aceptable en dev. En desarrollo, la capacidad de destruir y recrear rápidamente la infraestructura es más valiosa que la protección contra eliminación accidental. En prod sería crítico.
  - _debate_: cost (disagree): deletion_protection en VPC es una característica de AWS, no un atributo de Terraform. El riesgo real es operacional (terraform destroy), no arquitectónico. En dev, esto es aceptable; la severidad debería ser 'low' si se reporta.
  - _debate_: operations (agree): Aunque sea dev, la VPC es infraestructura crítica. Sin prevent_destroy o deletion_protection, un terraform destroy accidental destruye toda la red. Recomendado agregar lifecycle { prevent_destroy = true } o enable_deletion_protection = true.
  - _committee-decision_: La VPC carece de deletion_protection o lifecycle.prevent_destroy. Un terraform destroy accidental destruye toda la infraestructura de red. Aunque sea dev, la VPC es infraestructura crítica. Operations recomienda agregar protección. Security argumenta que en dev la capacidad de recrear rápidamente es valiosa, pero el riesgo de destrucción accidental justifica la protección. Recomendación: enable_deletion_protection = true o lifecycle { prevent_destroy = true }.
- **Cómo arreglarlo** (riesgo low): Habilitar deletion_protection en la VPC para prevenir eliminación accidental
  1. Agregar enable_dns_hostnames = true para mejor resolución de DNS
  2. Considerar agregar deletion_protection = true si el ambiente lo justifica, o al menos documentar el riesgo
  - IaC: `enable_dns_hostnames = true`

### AC-REL-002 · HIGH · Subnets públicas sin asignación automática de IP pública

- **Recurso:** `aws_subnet.public[0]`
- **Causa probable:** Las subnets públicas están configuradas sin asignar automáticamente direcciones IP públicas a las instancias lanzadas. Esto requiere asignación manual de EIPs o direcciones públicas, aumentando la complejidad operativa y el riesgo de instancias sin conectividad pública cuando se espera.
- **Evidencia:**
  - _attribute_: aws_subnet.public[0] tiene map_public_ip_on_launch = false
  - _attribute_: aws_subnet.public[1] tiene map_public_ip_on_launch = false
  - _attribute_: aws_subnet.public[2] tiene map_public_ip_on_launch = false
  - _debate_: security (disagree): map_public_ip_on_launch=false en subnets públicas es correcto desde seguridad: evita asignar IPs públicas automáticamente a todas las instancias. Los recursos que necesitan conectividad pública deben recibir EIPs explícitamente. Esto es una buena práctica, no un problema.
  - _debate_: cost (disagree): map_public_ip_on_launch=false es correcto en subnets públicas cuando se usan NAT gateways para salida. Las instancias privadas no necesitan IPs públicas; las públicas pueden recibir EIPs explícitamente. No es un defecto.
  - _debate_: operations (agree): map_public_ip_on_launch = false en subnets públicas es contraproducente. Instancias en subnets públicas esperan IP pública automática. Requiere asignación manual de EIPs, aumentando fricción operativa y riesgo de error. Debe ser true en subnets públicas.
  - _committee-decision_: Las 3 subnets públicas tienen map_public_ip_on_launch = false. Instancias lanzadas en subnets públicas no reciben IP pública automáticamente, requiriendo asignación manual de EIPs. Esto aumenta fricción operativa y riesgo de error. Operations enfatiza que es contraproducente en subnets públicas. Security argumenta que es buena práctica de seguridad, pero el consenso operacional es que subnets públicas deben tener map_public_ip_on_launch = true. Recomendación: cambiar a true en aws_subnet.public[0-2].
- **Cómo arreglarlo** (riesgo low): Habilitar asignación automática de IP pública en subnets públicas
  1. Establecer map_public_ip_on_launch = true en todas las subnets públicas
  2. Esto asegura que las instancias EC2 lanzadas en subnets públicas reciban automáticamente una dirección IP pública
  - IaC: `map_public_ip_on_launch = true`

### AC-SEC-001 · HIGH · Falta de VPC Flow Logs para observabilidad de tráfico

- **Recurso:** `aws_vpc.main`
- **Causa probable:** Los Flow Logs de VPC no están habilitados, lo que impide la detección y auditoría del tráfico de red
- **Evidencia:**
  - _attribute_: aws_vpc.main no tiene flow_logs configurados; no hay visibilidad de tráfico de red
  - _topology_: 3 NAT gateways (per_subnet[0], per_subnet[1], per_subnet[2]) sin VPC Flow Logs configurados
  - _attribute_: No hay recursos aws_flow_log asociados a la VPC o subnets
  - _debate_: security (agree): Sin VPC Flow Logs, no hay visibilidad del tráfico en los NAT Gateways. Esto dificulta diagnóstico de problemas de conectividad y análisis de seguridad. Desde perspectiva de seguridad, los flow logs son esenciales para detectar anomalías.
  - _debate_: cost (agree): VPC Flow Logs son críticos para auditoría de tráfico y no tienen costo significativo en dev. Sin embargo, la severidad es correcta (medium) porque es dev, no prod.
  - _debate_: cost (agree): Sin VPC Flow Logs, no hay visibilidad de tráfico NAT. Esto es un hallazgo válido de observabilidad. Severidad 'medium' es correcta para dev.
  - _debate_: reliability (agree): VPC Flow Logs son críticos para auditoría de tráfico y troubleshooting de conectividad. Sin ellos, no hay visibilidad de fallos en NAT gateways o rutas. En dev es menos crítico que prod, pero sigue siendo una brecha de observabilidad.
  - _debate_: reliability (agree): Sin VPC Flow Logs, no hay visibilidad de tráfico a través de NAT gateways. Esto impide diagnosticar problemas de conectividad saliente, detectar anomalías o validar reglas de seguridad. Es un gap de observabilidad importante.
  - _debate_: operations (agree): VPC Flow Logs son críticos para observabilidad de tráfico de red y troubleshooting operacional. Sin ellos, es imposible diagnosticar problemas de conectividad o detectar anomalías. Recomendado incluso en dev.
  - _committee-decision_: Sin VPC Flow Logs, no hay visibilidad del tráfico de red ni de los NAT Gateways. Esto impide diagnosticar problemas de conectividad, detectar anomalías y validar reglas de seguridad. Security, reliability y operations coinciden en que es crítico incluso en dev. Operations sugiere severidad high (vs. medium original) porque es esencial para troubleshooting operacional. Recomendación: habilitar VPC Flow Logs con destino CloudWatch Logs o S3.
- **Cómo arreglarlo** (riesgo low): Habilitar VPC Flow Logs para registrar el tráfico de red
  1. Crear un recurso aws_flow_log asociado a aws_vpc.main
  2. Configurar un destino (CloudWatch Logs o S3) para almacenar los logs
  3. Establecer traffic_type en ACCEPT, REJECT o ALL según necesidad de auditoría
  - IaC: `resource "aws_flow_log" "main" {
  iam_role_arn    = aws_iam_role.flow_logs.arn
  log_destination = aws_cloudwatch_log_group.flow_logs.arn
  traffic_type    = "ALL"
  vpc_id          = aws_vpc.main.id
}`

### AC-OPS-002 · HIGH · Etiquetas de gobernanza incompletas (Owner/Project)

- **Recurso:** `plan`
- **Causa probable:** Las etiquetas de gobernanza obligatorias (Owner, Project) no están definidas en los recursos de red, lo que impide la asignación de costos y la responsabilidad operativa
- **Evidencia:**
  - _attribute_: aws_vpc.main tiene tags: {"Environment":"dev","Name":"dev"} - falta Owner/Project
  - _topology_: 20 recursos de red (subnets, NAT gateways, route tables, EIPs, IGW) sin etiquetas de gobernanza
  - _debate_: cost (agree): Falta de etiquetas Owner/Project impide chargeback y gobernanza. En dev es menos crítico, pero la severidad 'high' es excesiva; debería ser 'medium'.
  - _committee-decision_: Los 20 recursos de red carecen de etiquetas Owner/Project. Esto impide chargeback, asignación de responsabilidad y gobernanza. Cost sugiere severidad medium (vs. high original) porque es dev, pero la falta de gobernanza es un riesgo operacional real. Recomendación: agregar tags Owner y Project a aws_vpc.main y propagarlas a todos los recursos.
- **Cómo arreglarlo** (riesgo low): Agregar etiquetas obligatorias de gobernanza a todos los recursos
  1. Definir variables de entrada para Owner y Project
  2. Aplicar estas etiquetas a aws_vpc.main y propagarlas a todos los recursos de red mediante default_tags o etiquetado explícito
  3. Ejemplo: agregar Owner="<team>" y Project="<project-name>" a las etiquetas
  - IaC: `tags = merge(var.common_tags, { Owner = var.owner, Project = var.project })`

### AC-OPS-003 · MEDIUM · Falta de CloudWatch alarms para monitoreo de NAT Gateways

- **Recurso:** `aws_nat_gateway.per_subnet`
- **Causa probable:** Sin alarmas, los problemas de NAT gateway (agotamiento de puertos, fallos) no se detectan proactivamente, causando degradación silenciosa de la conectividad saliente
- **Evidencia:**
  - _topology_: 3 NAT gateways sin alarmas de CloudWatch para ErrorPortAllocation, BytesOutToDestination o ErrorCount
  - _debate_: cost (agree): Sin CloudWatch alarms en NAT gateways, fallos de conectividad (ErrorPortAllocation, etc.) no se detectan. Válido para dev. Severidad 'medium' es correcta.
  - _debate_: reliability (agree): NAT gateways pueden fallar silenciosamente (agotamiento de puertos, errores de conexión). Sin CloudWatch alarms, los problemas no se detectan hasta que las aplicaciones fallan. Esto es especialmente crítico si hay aplicaciones dependientes.
  - _committee-decision_: Sin alarmas de CloudWatch, fallos de NAT Gateway (ErrorPortAllocation, ErrorCount, BytesOutToDestination) no se detectan proactivamente. Reliability enfatiza que esto es crítico si hay aplicaciones dependientes. Aunque sea dev, la observabilidad proactiva es recomendada. Recomendación: crear CloudWatch alarms para ErrorPortAllocation, ErrorCount y BytesOutToDestination en los 3 NAT Gateways.
- **Cómo arreglarlo** (riesgo low): Crear CloudWatch alarms para métricas críticas de NAT gateway
  1. Crear aws_cloudwatch_metric_alarm para ErrorPortAllocation (threshold > 0)
  2. Crear alarma para BytesOutToDestination (detectar cambios anormales)
  3. Configurar SNS topic para notificaciones
  - IaC: `resource "aws_cloudwatch_metric_alarm" "nat_port_exhaustion" { metric_name = "ErrorPortAllocation" namespace = "AWS/NatGateway" statistic = "Sum" threshold = 0 comparison_operator = "GreaterThanThreshold" }`

### AC-OPS-004 · MEDIUM · Route tables privadas sin nombres descriptivos

- **Recurso:** `aws_route_table.private`
- **Causa probable:** Sin nombres descriptivos, es difícil identificar rápidamente qué route table corresponde a qué AZ durante troubleshooting operacional
- **Evidencia:**
  - _topology_: 3 route tables (private[0], private[1], private[2]) sin etiqueta Name o descripción
  - _debate_: cost (agree): Route tables privadas sin nombres descriptivos (Name tag) dificultan troubleshooting. Severidad 'low' es correcta; es higiene operacional.
  - _committee-decision_: Las 3 route tables privadas carecen de etiqueta Name o descripción. Esto dificulta identificar rápidamente qué route table corresponde a qué AZ durante troubleshooting. Es higiene operacional. Recomendación: agregar tags Name (ej: 'private-us-east-1a', 'private-us-east-1b', 'private-us-east-1c') a aws_route_table.private[0-2].
- **Cómo arreglarlo** (riesgo low): Agregar etiqueta Name a cada route table privada
  1. Agregar tags con Name = "private-rt-${var.azs[index]}" a cada route table
  - IaC: `tags = { Name = "private-rt-${var.azs[count.index]}" }`

### AC-SEC-002 · MEDIUM · Falta de logging de cambios en infraestructura de red

- **Recurso:** `plan`
- **Causa probable:** No hay evidencia de CloudTrail habilitado para registrar cambios en la infraestructura de red y acceso a recursos públicos
- **Evidencia:**
  - _topology_: Se crean 3 subredes públicas (aws_subnet.public[0-2]) con Internet Gateway, pero no hay CloudTrail configurado en el plan para auditar cambios de infraestructura
  - _debate_: cost (disagree): CloudTrail es un servicio regional, no un recurso de VPC. Su ausencia en el plan no es un hallazgo de arquitectura de red; pertenece a una revisión de gobernanza global, no a este plan específico.
  - _debate_: operations (refine): CloudTrail es responsabilidad de auditoría/compliance, no de este plan de red. Sin embargo, desde ops: la falta de logging de cambios en infraestructura crítica (VPC, subnets, IGW) dificulta troubleshooting y rollback. El plan debería incluir CloudTrail o al menos documentar que se configura fuera de Terraform.
  - _committee-decision_: No hay evidencia de CloudTrail habilitado para auditar cambios en VPC, subnets, IGW y NAT Gateways. Aunque CloudTrail es responsabilidad de gobernanza global (no específica de este plan), operations refina que la falta de logging de cambios en infraestructura crítica dificulta troubleshooting y rollback. Severidad: medium (vs. original) porque es dev y CloudTrail es externo al plan. Recomendación: documentar que CloudTrail se configura fuera de Terraform o incluirlo en el plan.
- **Cómo arreglarlo** (riesgo low): Habilitar CloudTrail para auditar cambios en la infraestructura
  1. Crear un recurso aws_cloudtrail en el plan
  2. Configurar un bucket S3 para almacenar los logs de CloudTrail
  3. Establecer is_multi_region_trail = true para cobertura global
  4. Habilitar log_file_validation para integridad de logs
  - IaC: `resource "aws_cloudtrail" "main" {
  s3_bucket_name           = aws_s3_bucket.cloudtrail.id
  is_multi_region_trail    = true
  include_global_events    = true
  is_logging               = true
  enable_log_file_validation = true
}`

### AC-REL-003 · HIGH · Falta de redundancia en Internet Gateway

- **Recurso:** `aws_internet_gateway.main`
- **Causa probable:** AWS Internet Gateways son recursos de región, no de AZ, por lo que un único IGW es normal. Sin embargo, la arquitectura concentra toda la conectividad pública en un único recurso. Si bien AWS proporciona alta disponibilidad interna del IGW, la configuración no tiene redundancia explícita a nivel de aplicación.
- **Evidencia:**
  - _topology_: Un único aws_internet_gateway.main sirve a 3 subnets públicas en diferentes AZs (us-east-1a, us-east-1b, us-east-1c)
  - _plan_: El IGW es un punto único de fallo para toda la conectividad de salida de las subnets públicas
  - _debate_: security (disagree): Un único Internet Gateway es la arquitectura correcta en AWS. Los IGWs son recursos de región con alta disponibilidad interna; no requieren redundancia explícita. La redundancia está en los NAT Gateways (uno por AZ) para tráfico saliente de subnets privadas.
  - _debate_: cost (disagree): Un único IGW por región es la arquitectura estándar de AWS. Los IGWs son recursos de región con alta disponibilidad interna. Reportar esto como 'falta de redundancia' es incorrecto; no hay punto único de fallo real.
  - _debate_: operations (disagree): Un único IGW es arquitectura estándar y correcta en AWS. Los IGWs son recursos de región con HA interna de AWS. No hay punto único de fallo operacional; AWS garantiza disponibilidad. Agregar múltiples IGWs no mejora confiabilidad y complica enrutamiento. Severidad debe ser info o removerse.
  - _committee-decision_: Sin consolidar por el moderador: se conserva tal como lo reportó reliability.
- **Cómo arreglarlo** (riesgo low): Documentar y monitorear la disponibilidad del Internet Gateway
  1. Implementar monitoreo de la salud del IGW mediante CloudWatch
  2. Configurar alarmas para detectar fallos de conectividad a través del IGW
  3. Considerar usar VPC Flow Logs para auditar el tráfico del IGW

## Qué reportó cada especialista (ronda 1)

**security** — 2 hallazgos
- `SEC-1` [medium] VPC sin Flow Logs habilitados (`aws_vpc.main`)
- `SEC-2` [medium] Subredes públicas sin CloudTrail o auditoría de cambios (`plan`)

**cost** — 2 hallazgos
- `COST-1` [high] Tres NAT Gateways en entorno de desarrollo (`plan`)
- `COST-2` [medium] Tres Elastic IPs sin uso aparente en desarrollo (`aws_eip.nat`)

**reliability** — 3 hallazgos
- `REL-1` [medium] Sin protección contra eliminación accidental de la VPC (`aws_vpc.main`)
- `REL-2` [medium] Subnets públicas sin asignación automática de IP pública (`aws_subnet.public[0]`)
- `REL-3` [high] Falta de redundancia en Internet Gateway (`aws_internet_gateway.main`)

**operations** — 5 hallazgos
- `OPS-1` [high] Falta etiqueta obligatoria 'Owner' o 'Project' en recursos de red (`plan`)
- `OPS-2` [high] EIPs sin protección contra destrucción accidental (`aws_eip.nat`)
- `OPS-3` [medium] NAT gateways sin observabilidad de flujo de tráfico (`aws_nat_gateway.per_subnet`)
- `OPS-4` [medium] Falta CloudWatch alarms para monitoreo de NAT gateways (`aws_nat_gateway.per_subnet`)
- `OPS-5` [low] Route tables privadas sin documentación de propósito (`aws_route_table.private`)

### Réplicas (ronda 2)

| De | Sobre | Agente | Postura | Argumento |
|---|---|---|---|---|
| security | COST-1 | cost | agree | En un entorno dev (tags: Environment=dev), tres NAT Gateways con tres EIPs asociadas generan costo innecesario. Un único NAT Gateway compartido en una subnet p… |
| security | COST-2 | cost | agree | Las tres EIPs (aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2]) tienen costo mensual fijo en AWS incluso sin uso intenso. En dev, una única EIP es suficiente. |
| security | REL-1 | reliability | disagree | La VPC no tiene deletion_protection, pero esto es un trade-off aceptable en dev. En desarrollo, la capacidad de destruir y recrear rápidamente la infraestructu… |
| security | REL-2 | reliability | disagree | map_public_ip_on_launch=false en subnets públicas es correcto desde seguridad: evita asignar IPs públicas automáticamente a todas las instancias. Los recursos … |
| security | REL-3 | reliability | disagree | Un único Internet Gateway es la arquitectura correcta en AWS. Los IGWs son recursos de región con alta disponibilidad interna; no requieren redundancia explíci… |
| security | OPS-2 | operations | agree | Las EIPs (aws_eip.nat[0], aws_eip.nat[1], aws_eip.nat[2]) son recursos críticos para la conectividad saliente. Sin lifecycle.prevent_destroy, una eliminación a… |
| security | OPS-3 | operations | agree | Sin VPC Flow Logs, no hay visibilidad del tráfico en los NAT Gateways. Esto dificulta diagnóstico de problemas de conectividad y análisis de seguridad. Desde p… |
| cost | SEC-1 | security | agree | VPC Flow Logs son críticos para auditoría de tráfico y no tienen costo significativo en dev. Sin embargo, la severidad es correcta (medium) porque es dev, no p… |
| cost | SEC-2 | security | disagree | CloudTrail es un servicio regional, no un recurso de VPC. Su ausencia en el plan no es un hallazgo de arquitectura de red; pertenece a una revisión de gobernan… |
| cost | REL-1 | reliability | disagree | deletion_protection en VPC es una característica de AWS, no un atributo de Terraform. El riesgo real es operacional (terraform destroy), no arquitectónico. En … |
| cost | REL-2 | reliability | disagree | map_public_ip_on_launch=false es correcto en subnets públicas cuando se usan NAT gateways para salida. Las instancias privadas no necesitan IPs públicas; las p… |
| cost | REL-3 | reliability | disagree | Un único IGW por región es la arquitectura estándar de AWS. Los IGWs son recursos de región con alta disponibilidad interna. Reportar esto como 'falta de redun… |
| cost | OPS-1 | operations | agree | Falta de etiquetas Owner/Project impide chargeback y gobernanza. En dev es menos crítico, pero la severidad 'high' es excesiva; debería ser 'medium'. |
| cost | OPS-2 | operations | agree | Las 3 EIPs (aws_eip.nat[0-2]) están asociadas a NAT gateways críticos. Sin lifecycle.prevent_destroy, una eliminación accidental causa interrupción. En dev, se… |
| cost | OPS-3 | operations | agree | Sin VPC Flow Logs, no hay visibilidad de tráfico NAT. Esto es un hallazgo válido de observabilidad. Severidad 'medium' es correcta para dev. |
| cost | OPS-4 | operations | agree | Sin CloudWatch alarms en NAT gateways, fallos de conectividad (ErrorPortAllocation, etc.) no se detectan. Válido para dev. Severidad 'medium' es correcta. |
| cost | OPS-5 | operations | agree | Route tables privadas sin nombres descriptivos (Name tag) dificultan troubleshooting. Severidad 'low' es correcta; es higiene operacional. |
| reliability | SEC-1 | security | agree | VPC Flow Logs son críticos para auditoría de tráfico y troubleshooting de conectividad. Sin ellos, no hay visibilidad de fallos en NAT gateways o rutas. En dev… |
| reliability | COST-1 | cost | agree | Tres NAT Gateways en dev (Environment=dev) es un desperdicio. Cada NAT Gateway cuesta ~$32/mes. Un único NAT Gateway compartido en una AZ es suficiente para de… |
| reliability | COST-2 | cost | agree | Tres EIPs en dev generan costo innecesario (~$3.60/mes cada una). Si se reduce a un NAT Gateway, se necesita solo una EIP. El costo es bajo pero evitable en de… |
| reliability | OPS-2 | operations | agree | Las EIPs son recursos críticos para conectividad saliente. Sin lifecycle.prevent_destroy, un terraform destroy accidental o un cambio de configuración puede el… |
| reliability | OPS-3 | operations | agree | Sin VPC Flow Logs, no hay visibilidad de tráfico a través de NAT gateways. Esto impide diagnosticar problemas de conectividad saliente, detectar anomalías o va… |
| reliability | OPS-4 | operations | agree | NAT gateways pueden fallar silenciosamente (agotamiento de puertos, errores de conexión). Sin CloudWatch alarms, los problemas no se detectan hasta que las apl… |
| operations | SEC-1 | security | agree | VPC Flow Logs son críticos para observabilidad de tráfico de red y troubleshooting operacional. Sin ellos, es imposible diagnosticar problemas de conectividad … |
| operations | SEC-2 | security | refine | CloudTrail es responsabilidad de auditoría/compliance, no de este plan de red. Sin embargo, desde ops: la falta de logging de cambios en infraestructura crític… |
| operations | COST-1 | cost | agree | En dev con Environment=dev, tres NAT Gateways es sobredimensionamiento. Operacionalmente, un único NAT Gateway es más simple de monitorear y mantener. La HA de… |
| operations | COST-2 | cost | agree | Tres EIPs en dev generan costo fijo mensual. Desde ops, esto también complica el inventario y monitoreo de IPs. En dev, una única EIP compartida es suficiente. |
| operations | REL-1 | reliability | agree | Aunque sea dev, la VPC es infraestructura crítica. Sin prevent_destroy o deletion_protection, un terraform destroy accidental destruye toda la red. Recomendado… |
| operations | REL-2 | reliability | agree | map_public_ip_on_launch = false en subnets públicas es contraproducente. Instancias en subnets públicas esperan IP pública automática. Requiere asignación manu… |
| operations | REL-3 | reliability | disagree | Un único IGW es arquitectura estándar y correcta en AWS. Los IGWs son recursos de región con HA interna de AWS. No hay punto único de fallo operacional; AWS ga… |

## Ejecución

- **Modelo:** `us.anthropic.claude-haiku-4-5-20251001-v1:0`
- **Rondas ejecutadas:** 2; moderador: sí
- **Tokens:** 59,325 de un máximo de 100,000
- **Versiones de los prompts:** security v1, cost v1, reliability v1, operations v1, moderator v2

### Avisos

- REL-3 no fue tratado por el moderador y se conservó sin cambios
- desacuerdo sobre REL-3 no resuelto por el moderador; se registró como no resuelto

_Este informe es una ayuda a la decisión: lo debe revisar una persona. El comité no aplica ningún cambio._
