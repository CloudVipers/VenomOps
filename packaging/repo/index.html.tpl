<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VenomOps: repositorio de paquetes</title>
<style>
  body { font: 16px/1.5 system-ui, sans-serif; max-width: 46rem; margin: 2rem auto; padding: 0 1rem; color: #1f2328; }
  pre { background: #f6f8fa; padding: .8rem 1rem; overflow-x: auto; border-radius: 6px; font-size: 14px; }
  @media (prefers-color-scheme: dark) { body { background: #0d1117; color: #e6edf3; } pre { background: #161b22; } a { color: #58a6ff; } }
</style>
</head>
<body>
<h1>VenomOps: repositorio de paquetes</h1>
<p>Repositorio firmado con GPG de <code>venom</code> (incluye <code>kubectl-venom_doctor</code>). Versión publicada: <strong>${VERSION}</strong>.
Código y documentación: <a href="https://github.com/CloudVipers/VenomOps">github.com/CloudVipers/VenomOps</a>.</p>

<h2>RHEL 9, Rocky, Alma, Amazon Linux 2023</h2>
<pre>sudo tee /etc/yum.repos.d/venom.repo &lt;&lt;'REPO'
[venom]
name=VenomOps
baseurl=${URL}/rpm/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=${URL}/venom-repo.asc
REPO
sudo dnf install venom</pre>

<h2>Debian 12+, Ubuntu 22.04+</h2>
<pre>sudo curl -fsSL ${URL}/venom-repo.gpg -o /usr/share/keyrings/venom.gpg
echo 'deb [signed-by=/usr/share/keyrings/venom.gpg] ${URL}/deb stable main' | sudo tee /etc/apt/sources.list.d/venom.list
sudo apt update &amp;&amp; sudo apt install venom</pre>

<p>Clave pública: <a href="venom-repo.asc">venom-repo.asc</a> (rpm) y <a href="venom-repo.gpg">venom-repo.gpg</a> (apt).</p>
<p><small>Este repositorio solo contiene la última versión. <code>venom fix</code> necesita <code>terraform</code> en el <code>PATH</code>.</small></p>
</body>
</html>
