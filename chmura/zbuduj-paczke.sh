#!/usr/bin/env bash
# Buduje paczkę chmury Nati do instalacji na serwerze:  dist/nati-chmura-<wersja>.tar.gz
#   (kod serwera + Dockerfile + install.sh / instaluj.sh + instrukcje + plik dla NAS-ów). Na serwerze:  tar xzf … && cd nati-chmura-… && ./instaluj.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/server/app/__init__.py")"
NAME="nati-chmura-$VERSION"
OUT="$ROOT/dist"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/$NAME/server" "$TMP/$NAME/docs" "$OUT"
cp "$ROOT/server/Dockerfile" "$ROOT/server/pyproject.toml" "$TMP/$NAME/server/"
cp -r "$ROOT/server/app" "$TMP/$NAME/server/"
find "$TMP/$NAME/server" -name __pycache__ -type d -prune -exec rm -rf {} +
printf '__pycache__\n*.pyc\n' > "$TMP/$NAME/server/.dockerignore"
sed "s/__WERSJA__/$VERSION/" "$ROOT/chmura/install.sh" > "$TMP/$NAME/install.sh"
chmod +x "$TMP/$NAME/install.sh"
ln -s install.sh "$TMP/$NAME/instaluj.sh"   # polska nazwa – ten sam instalator
for f in CZYTAJ.md README.md; do sed "s/__WERSJA__/$VERSION/g" "$ROOT/chmura/$f" > "$TMP/$NAME/$f"; done
cp "$ROOT/chmura/docker-compose.nas.yml" "$TMP/$NAME/"
cp "$ROOT/LICENSE-server" "$TMP/$NAME/LICENSE"  # serwer jest open source (GPL-3.0-or-later)
cp "$ROOT/docs/bezpieczenstwo.md" "$TMP/$NAME/docs/"

tar -C "$TMP" -czf "$OUT/$NAME.tar.gz" "$NAME"
echo "Gotowe: $OUT/$NAME.tar.gz ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))"
