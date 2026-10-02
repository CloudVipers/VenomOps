# Instalación

VenomOps se instala de tres formas. Elige según lo que necesites:

| Quiero… | Instala | Cómo |
|---|---|---|
| **Todo** (`venom doctor`, `venom fix`, `venom review`) en un servidor Linux | el paquete `venom` | [Repositorio firmado](#paquete-rpm-o-deb-recomendado) (`dnf` / `apt`) |
| Solo **diagnosticar un clúster** desde mi equipo (Linux, macOS o Windows) | el plugin `venom-doctor` | [krew](#plugin-de-kubectl-con-krew) o [binario](#binario-suelto) |
| **Desarrollar** o probar la última versión | el código fuente | [Desde el repositorio](#desde-el-código-fuente) |

## Paquete rpm o deb (recomendado)

Instala `venom` y `kubectl-venom_doctor` **sin necesitar Python** ni nada más. Los paquetes están firmados con GPG y el
repositorio se verifica en cada actualización.

**RHEL 9, Rocky, AlmaLinux, Amazon Linux 2023** (x86_64 y aarch64):

```bash
sudo tee /etc/yum.repos.d/venom.repo <<'REPO'
[venom]
name=VenomOps
baseurl=https://cloudvipers.github.io/VenomOps/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://cloudvipers.github.io/VenomOps/venom-repo.asc
REPO
sudo dnf install venom
```

**Debian 12+, Ubuntu 22.04+** (amd64 y arm64):

```bash
sudo curl -fsSL https://cloudvipers.github.io/VenomOps/venom-repo.gpg -o /usr/share/keyrings/venom.gpg
echo 'deb [signed-by=/usr/share/keyrings/venom.gpg] https://cloudvipers.github.io/VenomOps/deb stable main' | sudo tee /etc/apt/sources.list.d/venom.list
sudo apt update && sudo apt install venom
```

> **Comprueba la clave.** La primera vez, `dnf` te muestra la huella de la clave de firma y te pide confirmar. Debe ser
> exactamente `A7BE 1F5E 03EC 7AA9 C797 A9DE 3237 E8D7 9E6E 29C5` (`VenomOps Packages`). Si es otra, no continúes.

Actualizar y desinstalar son los comandos normales de tu gestor de paquetes, o el propio `venom`:

```bash
sudo dnf upgrade venom        # o: sudo apt install --only-upgrade venom
venom update --check          # ¿hay una versión nueva?
venom update --install        # descarga, verifica la firma e instala (ver la guía de uso)
sudo dnf remove venom         # o: sudo apt remove venom
```

## Plugin de kubectl con krew

[krew](https://krew.sigs.k8s.io/) es el gestor de plugins de `kubectl`. Funciona en Linux, macOS y Windows y solo instala
`venom-doctor` (el diagnóstico de clústeres):

```bash
kubectl krew install --manifest-url=https://raw.githubusercontent.com/CloudVipers/VenomOps/main/packages/venom-doctor/venom-doctor.yaml
kubectl venom-doctor --version
```

> El plugin está propuesto al índice oficial de krew. Cuando lo acepten bastará `kubectl krew install venom-doctor`; mientras
> tanto, el comando de arriba hace lo mismo desde este repositorio, con la misma comprobación de `sha256`.

## Binario suelto

Cada [release](https://github.com/CloudVipers/VenomOps/releases) trae el binario de `venom-doctor` para Linux (amd64 y arm64),
macOS (Intel y Apple Silicon) y Windows (amd64), con su `checksums.txt`:

```bash
VERSION=0.1.5
curl -fsSLO https://github.com/CloudVipers/VenomOps/releases/download/venom-doctor-v${VERSION}/venom-doctor_v${VERSION}_linux_amd64.tar.gz
curl -fsSLO https://github.com/CloudVipers/VenomOps/releases/download/venom-doctor-v${VERSION}/checksums.txt
sha256sum -c checksums.txt --ignore-missing          # en macOS: shasum -a 256 -c checksums.txt --ignore-missing
tar -xzf venom-doctor_v${VERSION}_linux_amd64.tar.gz
sudo install kubectl-venom_doctor /usr/local/bin/    # kubectl lo detecta como `kubectl venom-doctor`
```

## Desde el código fuente

Para contribuir o probar `main`. Necesitas Go 1.26+ (para `venom-doctor`) y Python 3.12+ (para el resto):

```bash
git clone https://github.com/CloudVipers/VenomOps.git && cd VenomOps
cd packages/venom-doctor && make build && sudo install bin/kubectl-venom_doctor /usr/local/bin/ && cd ../..
cd packages/venom && make setup && .venv/bin/venom --help
```

## Qué más necesitas según lo que uses

| Para… | Necesitas |
|---|---|
| `venom doctor` | Un `kubeconfig` con acceso de lectura al clúster. Los [permisos exactos](uso.md#permisos-que-necesita-en-el-clúster) están documentados |
| `venom fix` | `terraform` en el `PATH` (valida el arreglo) y, para abrir el PR de verdad, un `GITHUB_TOKEN` |
| `--explain`, `--agent`, `venom review` | Credenciales de AWS con acceso a Amazon Bedrock y **un modelo indicado por ti** (no hay ninguno por defecto) |

Nada de esto es obligatorio para diagnosticar: **`venom doctor` sin `--explain` no usa IA ni sale a ningún servicio externo**.

## Comprobar que funciona

```bash
venom --version                  # venom 0.1.5
kubectl-venom_doctor --version   # kubectl-venom_doctor version 0.1.5
kubectl venom-doctor -n kube-system   # diagnostica un namespace de tu clúster actual
```

Si algo falla, mira la [ayuda y solución de problemas](ayuda.md).
