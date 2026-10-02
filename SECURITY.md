# Política de seguridad

## Versiones con soporte

Se corrige la **última versión publicada** (consulta los [releases](https://github.com/CloudVipers/VenomOps/releases)). El proyecto es joven y no mantiene ramas de
versiones anteriores.

## Cómo reportar una vulnerabilidad

Usa el **aviso de seguridad privado** de GitHub: pestaña *Security* → *Report a vulnerability* del repositorio
(<https://github.com/CloudVipers/VenomOps/security/advisories/new>). **No abras una incidencia pública** con los detalles.

Incluye, si puedes:

- qué componente (`venom-doctor`, `pr-agent`, `arch-committee`, `venom`, los paquetes o el repositorio firmado) y qué versión;
- cómo reproducirlo y qué impacto tendría;
- si hay credenciales o datos reales implicados (no los adjuntes: describe dónde aparecen).

## Qué consideramos una vulnerabilidad

Especialmente: cualquier forma de que una herramienta **modifique infraestructura** cuando no debe (ejecutar `terraform apply`/`destroy` o un comando mutante de `kubectl`, mezclar un PR o
empujar a `main`), de que **un secreto o un ID de cuenta salga** hacia un modelo, un registro o un PR sin enmascarar, de que una llamada a un modelo ocurra **sin que se haya pedido**, o de que un
paquete o índice manipulado **pase** la verificación de firma.

Las garantías y dónde se hacen cumplir están en la [guía de seguridad](docs/guia/seguridad.md).
