#!/usr/bin/env bash
# Paczka „Chmura na tym komputerze”:  dist/nati-chmura-linux-x86_64-<wersja>.tar.gz
#
# Serwer Nati bez Dockera i bez niczego w systemie – aplikacja pobiera tę paczkę, rozpakowuje do
# ~/.local/share/nati-chmura/app i uruchamia jako usługę systemd użytkownika. W środku:
#   python/   – przenośny Python 3.13 (python-build-standalone) z serwerem i zależnościami,
#   ffmpeg/   – ffmpeg + ffprobe 7.1 (GPL, tylko dźwięk; transkodowanie i yt-dlp),
#   bin/qjs   – QuickJS-ng (silnik JS dla yt-dlp/YouTube, 2 MB zamiast ~100 MB deno/node),
#   bin/nati-chmura – uruchomienie (zmienne NATI_*, port NATI_PORT, domyślnie 8080).
# Serwer jest open source (GPL) – licencje w licenses/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$ROOT/pakiety/linux"
WORK="${NATI_BUILD_DIR:-$HERE/.budowanie}"
ENGINE="$(command -v podman || command -v docker)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/server/pyproject.toml")"
IMAGE=nati-build:22.04
OUT="$ROOT/dist/nati-chmura-linux-x86_64-$VERSION.tar.gz"

PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.13.18%2B20261110-x86_64-unknown-linux-gnu-install_only.tar.gz"
QJS_URL="https://github.com/quickjs-ng/quickjs/releases/download/v0.18.0/qjs-linux-x86_64"
FFMPEG=7.1.2

info() { printf '\033[1;35m♪\033[0m %s\n' "$*"; }
mkdir -p "$WORK"/{src,chmura} "$ROOT/dist"

info "Obraz budowania ($IMAGE)…"
"$ENGINE" build -q -t "$IMAGE" -f "$HERE/Containerfile" "$HERE" >/dev/null

info "Chmura $VERSION (Python, serwer, ffmpeg, QuickJS) w kontenerze…"
rm -rf "$WORK/chmura/out"
"$ENGINE" run --rm -v "$WORK/src:/src:Z" -v "$WORK/chmura:/w:Z" -v "$ROOT/server:/server:ro,Z" \
  -e PY_URL="$PY_URL" -e QJS_URL="$QJS_URL" -e FFMPEG="$FFMPEG" "$IMAGE" bash -euo pipefail -c '
    OUT=/w/out/nati-chmura; mkdir -p $OUT/bin $OUT/licenses
    # Python + serwer
    [[ -f /src/python.tar.gz ]] || curl -fsSL -o /src/python.tar.gz "$PY_URL"
    tar xzf /src/python.tar.gz -C $OUT
    cp -r /server /tmp/server && rm -rf /tmp/server/.venv /tmp/server/data /tmp/server/tests
    $OUT/python/bin/python3 -m pip install --no-cache-dir --no-compile -q /tmp/server
    rm -rf $OUT/python/lib/python3.13/test $OUT/python/lib/python3.13/idlelib $OUT/python/lib/python3.13/tkinter \
           $OUT/python/lib/python3.13/turtledemo $OUT/python/lib/libtcl* $OUT/python/lib/libtk* $OUT/python/lib/tcl* \
           $OUT/python/lib/tk* $OUT/python/include
    # pip zostaje – serwer aktualizuje nim yt-dlp raz na dobę (pip install --target).
    rm -rf $OUT/python/lib/python3.13/ensurepip \
           $OUT/python/lib/python3.13/config-3.13-x86_64-linux-gnu $OUT/python/lib/python3.13/pydoc_data \
           $OUT/python/lib/itcl* $OUT/python/lib/thread* $OUT/python/bin/idle* $OUT/python/bin/pydoc*
    find $OUT/python -name __pycache__ -prune -exec rm -rf {} +
    # Bez symboli debugowania (sam libpython: 140 MB → kilkanaście)
    find $OUT/python -type f \( -name "*.so" -o -name "*.so.*" \) -exec strip --strip-unneeded {} + 2>/dev/null || true
    strip --strip-unneeded $OUT/python/bin/python3.13 2>/dev/null || true
    # ffmpeg (GPL): tylko to, czego chmura potrzebuje – dźwięk, Opus, kontenery muzyczne
    cd /src
    [[ -d ffmpeg-$FFMPEG ]] || curl -fsSL https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz | tar xJ
    rm -rf /tmp/ff && mkdir /tmp/ff && cd /tmp/ff
    /src/ffmpeg-$FFMPEG/configure --prefix=/opt/ff --enable-gpl --enable-libopus --enable-libmp3lame \
      --disable-ffplay --disable-doc --disable-debug --disable-avdevice --disable-network --disable-autodetect \
      --disable-hwaccels --disable-vaapi --disable-vdpau --disable-xlib --disable-libxcb \
      --enable-shared --disable-static >/dev/null
    make -j"$(nproc)" >/dev/null && make install >/dev/null
    mkdir -p $OUT/ffmpeg/bin $OUT/ffmpeg/lib
    cp /opt/ff/bin/ffmpeg /opt/ff/bin/ffprobe $OUT/ffmpeg/bin/
    cp -a /opt/ff/lib/lib*.so.* $OUT/ffmpeg/lib/
    cp -L /usr/lib/x86_64-linux-gnu/libopus.so.0 /usr/lib/x86_64-linux-gnu/libmp3lame.so.0 $OUT/ffmpeg/lib/
    strip -s $OUT/ffmpeg/bin/* $OUT/ffmpeg/lib/* 2>/dev/null || true
    # Biblioteki obok programów – bez LD_LIBRARY_PATH (który zmieniałby też biblioteki Pythona).
    for f in $OUT/ffmpeg/bin/*; do patchelf --set-rpath "\$ORIGIN/../lib" "$f"; done
    for f in $OUT/ffmpeg/lib/*; do patchelf --set-rpath "\$ORIGIN" "$f"; done
    cp /src/ffmpeg-$FFMPEG/COPYING.GPLv2 $OUT/licenses/ffmpeg-GPL-2.0.txt
    # QuickJS-ng
    curl -fsSL -o $OUT/bin/qjs "$QJS_URL" && chmod +x $OUT/bin/qjs
    curl -fsSL -o $OUT/licenses/quickjs-ng-MIT.txt https://raw.githubusercontent.com/quickjs-ng/quickjs/v0.18.0/LICENSE
    cp $OUT/python/lib/python3.13/LICENSE.txt $OUT/licenses/python-PSF.txt 2>/dev/null || true
  '

info "Uruchamianie i pakowanie…"
OUTDIR="$WORK/chmura/out/nati-chmura"
cat > "$OUTDIR/bin/nati-chmura" <<'EOF'
#!/bin/sh
# Nati – chmura na tym komputerze (bez Dockera). Ustawienia przez zmienne NATI_* (patrz server/app/config.py).
HERE="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
export PATH="$HERE/bin:$HERE/ffmpeg/bin:$PATH"
export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
exec "$HERE/python/bin/python3" -m uvicorn app.main:app --host "${NATI_HOST:-0.0.0.0}" --port "${NATI_PORT:-8080}" "$@"
EOF
chmod +x "$OUTDIR/bin/nati-chmura"
echo "$VERSION" > "$OUTDIR/VERSION"
cp "$ROOT/LICENSE-server" "$OUTDIR/licenses/nati-server-GPL-3.0.txt" 2>/dev/null || true
tar czf "$OUT.new" -C "$WORK/chmura/out" nati-chmura && mv -f "$OUT.new" "$OUT"
info "Gotowe: $OUT ($(du -h "$OUT" | cut -f1); po rozpakowaniu $(du -sh "$OUTDIR" | cut -f1))"
