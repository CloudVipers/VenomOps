#!/usr/bin/env bash
# Runs INSIDE a Rocky Linux 9 container (glibc 2.34, the oldest we support: the binary also runs on Amazon Linux 2023, Debian 12+ and Ubuntu 22.04+).
# Input:  /src (read-only monorepo), /out (output directory)
# Output: /out/venom-dist/  (PyInstaller onedir with the `venom` executable)
set -euo pipefail

dnf install -y -q python3.12 python3.12-pip binutils tar gzip >/dev/null
work=$(mktemp -d)
tar -C /src --exclude='.venv' --exclude='dist' --exclude='__pycache__' --exclude='.git' --exclude='node_modules' \
  -cf - packages packaging | tar -C "$work" -xf -
cd "$work"

python3.12 -m venv /tmp/venv
/tmp/venv/bin/pip install -q --upgrade pip
# Regular (non-editable) installs: this also proves the wheels are self-contained (e.g. the schema ships inside).
/tmp/venv/bin/pip install -q pyinstaller \
  ./packages/findings-schema/python ./packages/pr-agent ./packages/arch-committee ./packages/venom

/tmp/venv/bin/pyinstaller --noconfirm --clean --name venom --onedir \
  --collect-data findings_schema --collect-data arch_committee --collect-data venom \
  --collect-all hcl2 --collect-data jsonschema --collect-data jsonschema_specifications \
  --collect-data botocore --collect-submodules pr_agent --collect-submodules arch_committee \
  --distpath /out/pyi --workpath /tmp/pyi-work --specpath /tmp/pyi-spec \
  packaging/venom_entry.py

rm -rf /out/venom-dist && mv /out/pyi/venom /out/venom-dist && rmdir /out/pyi
/out/venom-dist/venom --version
# The container runs as root; hand the files back to the invoking user so the host can clean them up.
chown -R "${HOST_UID:-0}:${HOST_GID:-0}" /out
