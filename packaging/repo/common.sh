#!/usr/bin/env bash
# Sourced inside the containers: imports the private key into a throwaway keyring and sets GPG helpers.
export GNUPGHOME=/tmp/gnupg; mkdir -p "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
printf '%s' "${GPG_PASSPHRASE:-}" > /tmp/pass; chmod 600 /tmp/pass
GPG="gpg --batch --yes --pinentry-mode loopback --passphrase-file /tmp/pass"
$GPG --import /key.asc 2>/dev/null
KEYID=$(gpg --list-secret-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}')
[ -n "$KEYID" ] || { echo "could not import the signing key" >&2; exit 1; }
echo "signing key: ${KEYID: -16}"
finish() { rm -f /tmp/pass; chown -R "${HOST_UID:-0}:${HOST_GID:-0}" /out; }
trap finish EXIT
