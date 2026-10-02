# Repositorio yum/dnf y apt firmado de `venom`

Genera un **sitio estático** con un repositorio rpm (`dnf`/`yum`) y otro deb (`apt`), con paquetes y metadatos
firmados con GPG, para poder instalar con `sudo dnf install venom` / `sudo apt install venom` sin pegar una URL.
**No publica nada**: la carpeta de salida se aloja donde se decida (GitHub Pages, S3, cualquier servidor web).

## Generar

```bash
# 1. Paquetes de las arquitecturas que haya (packaging/build.sh o el artefacto del workflow release-venom.yml)
# 2. La clave PRIVADA de firma (ver «Clave de firma»); nunca va al repositorio
GPG_KEY_FILE=~/venom-signing.asc GPG_PASSPHRASE='...' ./packaging/repo/build-repo.sh packaging/dist ./site
```

Salida: `site/rpm/<x86_64|aarch64>/` (con `repodata/` firmado), `site/deb/` (`pool/` y `dists/stable/` con `InRelease` y
`Release.gpg`), y la clave pública en `site/venom-repo.asc` (rpm) y `site/venom-repo.gpg` (apt). Se construye en contenedores
Rocky 9 y Debian 12 desechables y la clave no sale de ellos.

## Probar (sin tocar nada público)

```bash
PKG_DIR=packaging/dist ./packaging/repo/test-repo.sh
```

Genera una clave **desechable** con contraseña, construye el repositorio, lo sirve por HTTP en una red de Docker y:

- instala `venom` con el `dnf` y el `apt` reales (Rocky 9, Amazon Linux 2023, Debian 12 y Ubuntu 24.04) con
  `gpgcheck=1` y `repo_gpgcheck=1` / `signed-by`;
- comprueba que se **rechazan**, por el motivo correcto, un cliente sin la clave (rpm y apt), un paquete rpm manipulado y un
  índice `Packages.gz` de apt manipulado.

Si hay paquetes arm64 en el directorio, el repositorio se genera también para esa arquitectura (la instalación solo se prueba
en la arquitectura de la máquina que ejecuta la prueba).

## Clave de firma (decisión y custodia)

La clave privada es lo que da confianza al repositorio: quien la tenga puede publicar paquetes que los clientes aceptarán.

```bash
gpg --quick-generate-key "VenomOps Packages <packages@example.com>" rsa4096 sign 2y
gpg --armor --export-secret-keys <ID> > venom-signing.asc      # guardar en un gestor de secretos, NO en git
gpg --armor --export <ID> > venom-repo.asc                      # la pública sí se publica
```

- Guardar la privada y su contraseña como **secretos del repositorio** (por ejemplo `GPG_SIGNING_KEY` y `GPG_PASSPHRASE`)
  y usarlos solo desde el workflow que publica.
- Poner caducidad (2 años) y renovarla antes: al rotarla, los clientes deben importar la clave nueva.
- Si la clave se filtra: revocarla (`gpg --gen-revoke`), publicar una nueva y avisar; los paquetes ya firmados con la vieja
  dejan de ser de fiar.

## Instalar (cuando el sitio esté alojado en `<URL>`)

```bash
# RHEL 9 / Rocky / Alma / Amazon Linux 2023
sudo tee /etc/yum.repos.d/venom.repo <<'REPO'
[venom]
name=VenomOps
baseurl=<URL>/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=<URL>/venom-repo.asc
REPO
sudo dnf install venom

# Debian 12+ / Ubuntu 22.04+
sudo curl -fsSL <URL>/venom-repo.gpg -o /usr/share/keyrings/venom.gpg
echo 'deb [signed-by=/usr/share/keyrings/venom.gpg] <URL>/deb stable main' | sudo tee /etc/apt/sources.list.d/venom.list
sudo apt update && sudo apt install venom
```

## Publicar en GitHub Pages (mismo repositorio)

El workflow [`publish-repo.yml`](../../.github/workflows/publish-repo.yml) toma los paquetes de un release `venom-vX.Y.Z`,
regenera el repositorio firmado y lo despliega en `https://<org>.github.io/<repo>/` (hoy
`https://cloudvipers.github.io/VenomOps/`, con una página de inicio con las instrucciones). Es solo manual y el repositorio
contiene **únicamente la última versión publicada**.

**Puesta en marcha (una vez, con permisos de administración):**

```bash
# 1. Clave de firma, en tu máquina (te pedirá la contraseña); apunta el ID largo que muestra
gpg --quick-generate-key "VenomOps Packages <tu-correo>" rsa4096 sign 2y
gpg --list-secret-keys --keyid-format long

# 2. Exportarla y guardarla como secretos del repositorio
gpg --armor --export-secret-keys <ID> > ~/venom-signing.asc
gh secret set GPG_SIGNING_KEY --repo CloudVipers/VenomOps < ~/venom-signing.asc
gh secret set GPG_PASSPHRASE  --repo CloudVipers/VenomOps          # pega la contraseña cuando la pida
# Haz una copia de la clave en tu gestor de contraseñas ANTES de borrar el archivo; sin ella no se podrá rotar ni revocar.
shred -u ~/venom-signing.asc

# 3. Activar Pages con «GitHub Actions» como origen
gh api -X POST repos/CloudVipers/VenomOps/pages -f build_type=workflow
```

**Cada release** (después de subir los `.rpm`/`.deb` a `venom-vX.Y.Z`):

```bash
gh workflow run publish-repo.yml --repo CloudVipers/VenomOps -f tag=venom-v0.1.3 -f publish=true
```

Con `publish=false` (por defecto) hace un **ensayo**: construye el repositorio con una clave desechable, instala `venom` con
el `dnf` y el `apt` reales y comprueba los rechazos, sin publicar nada y sin necesitar los secretos. Úsalo para validar el
cableado tras cambiar el empaquetado.
