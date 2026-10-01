# Paquetes de `venom` (.rpm y .deb)

Instalan `venom` y `kubectl-venom_doctor` **sin necesitar Python** en la máquina de destino. Los paquetes traen un bundle
autocontenido (PyInstaller) en `/usr/lib/venom/`, con `/usr/bin/venom` y `/usr/bin/kubectl-venom_doctor`.

## Instalar

```bash
# RHEL 9 / Rocky / Alma / Amazon Linux 2023 (desde un release de GitHub)
sudo dnf install https://github.com/CloudVipers/VenomOps/releases/download/venom-v0.1.2/venom-0.1.2-1.x86_64.rpm

# Debian 12+ / Ubuntu 22.04+
curl -LO https://github.com/CloudVipers/VenomOps/releases/download/venom-v0.1.2/venom_0.1.2_amd64.deb
sudo apt install ./venom_0.1.2_amd64.deb

venom --version
```

`venom fix` necesita `terraform` en el `PATH` para validar el arreglo (no se instala con el paquete). Las llamadas a
Bedrock siguen siendo opcionales y explícitas; el paquete no cambia ninguna regla de seguridad.

## Construir en local

```bash
go install github.com/goreleaser/nfpm/v2/cmd/nfpm@latest   # una vez
./packaging/build.sh                                        # deja los paquetes en packaging/dist/
```

Requiere `docker`, `go` y `nfpm`. El script compila `venom` dentro de un contenedor **Rocky Linux 9** (glibc 2.34, la más
antigua soportada: el mismo binario corre en AL2023, Debian 12+ y Ubuntu 22.04+; construir en una distro más nueva
produciría un binario que falla en RHEL 9) y `kubectl-venom_doctor` como binario Go estático. No instala ni publica nada.
Probado en: Rocky 9, AlmaLinux 9, Amazon Linux 2023, Debian 12, Ubuntu 22.04 y 24.04 (x86_64).

## Publicar

El workflow [`release-venom.yml`](../.github/workflows/release-venom.yml) construye los paquetes x86_64 y arm64 al
empujar un tag `venom-vX.Y.Z` y los adjunta al release. Para que funcione `sudo yum install venom` (sin URL) hace falta
además un **repositorio yum** (metadatos `createrepo_c` firmados con GPG, p. ej. en GitHub Pages o S3); eso requiere una
clave de firma y es una decisión aparte.
