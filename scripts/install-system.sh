#!/usr/bin/env bash
# Install an administrator-owned CLI without changing shared Conda packages.
# Usage: sudo bash scripts/install-system.sh [python3-path]
set -euo pipefail
umask 022

if [[ $(id -u) != 0 ]]; then
  printf 'Run this installer as root.\n' >&2
  exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
python_bin=${1:-python3}
prefix=/opt/hygonstat
entry=/usr/local/bin/hygonstat
if [[ -e "$prefix" && (! -d "$prefix" || -L "$prefix") ]]; then
  printf 'Refusing unexpected installation path: %s\n' "$prefix" >&2
  exit 1
fi
if [[ -e "$entry" || -L "$entry" ]]; then
  if ! grep -Fq '# hygonstat system launcher' "$entry"; then
    printf 'Refusing to replace an unrelated command: %s\n' "$entry" >&2
    exit 1
  fi
fi
for stable in current venv src; do
  if [[ -e "$prefix/$stable" && ! -L "$prefix/$stable" ]]; then
    printf 'Refusing unexpected non-symlink path: %s/%s\n' "$prefix" "$stable" >&2
    exit 1
  fi
done

install -d -o root -g root -m 0755 "$prefix" "$prefix/releases" /usr/local/bin
release=$(mktemp -d "$prefix/releases/install-XXXXXXXX")
chmod 0755 "$release"
install -d -o root -g root -m 0755 "$release/src"
# Only project files enter the administrator-owned source snapshot.
tar -C "$source_dir" --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='._*' --exclude='.DS_Store' \
  -cf - pyproject.toml MANIFEST.in README.md LICENSE NOTICE ISSUES.md \
  hygonstat tests scripts .github |
  tar -C "$release/src" -xf -
chown -R root:root "$release/src"
find "$release/src" -type d -exec chmod 0755 {} +
find "$release/src" -type f -exec chmod 0644 {} +
chmod 0755 "$release/src/scripts/install-system.sh"

"$python_bin" -I -m venv "$release/venv"
"$release/venv/bin/python" -I -m pip install --disable-pip-version-check \
  --no-cache-dir "$release/src"
(cd "$release/src" && "$release/venv/bin/python" -I -m unittest discover -s tests -v)
"$release/venv/bin/python" -I -m hygonstat --version
"$release/venv/bin/python" -I -m pip freeze > "$release/requirements-installed.txt"
chown -R root:root "$release"
chmod -R go-w "$release"

# Publish only after package installation and offline tests have succeeded.
previous=$(readlink "$prefix/current" || true)
if [[ -n "$previous" ]]; then
  printf '%s\n' "$previous" > "$release/previous-release.txt"
fi
ln -s "releases/$(basename "$release")" "$prefix/current.new"
mv -Tf "$prefix/current.new" "$prefix/current"
for stable in venv src; do
  ln -s "current/$stable" "$prefix/$stable.new"
  mv -Tf "$prefix/$stable.new" "$prefix/$stable"
done
launcher=$(mktemp /usr/local/bin/.hygonstat-XXXXXXXX)
cat > "$launcher" <<'LAUNCHER'
#!/bin/sh
# hygonstat system launcher
exec /opt/hygonstat/venv/bin/python -I -m hygonstat "$@"
LAUNCHER
chown root:root "$launcher"
chmod 0755 "$launcher"
mv -f "$launcher" "$entry"
printf 'Installed: %s\nRelease: %s\nSource: %s/src\n' "$entry" "$release" "$prefix"
if [[ -n "$previous" ]]; then
  printf 'Previous release retained at: %s/%s\n' "$prefix" "$previous"
fi
