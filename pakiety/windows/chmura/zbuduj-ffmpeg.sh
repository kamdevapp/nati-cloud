#!/usr/bin/env bash
# ffmpeg + ffprobe dla Windowsa (x64), tylko dźwięk – do paczki „Chmura na tym komputerze”.
# Budowanie krzyżowe na Linuksie (mingw-w64); biblioteki ffmpeg jako DLL obok programów (wspólne
# dla ffmpeg i ffprobe), opus i lame wkompilowane, bez zależności od bibliotek mingw.
# Wymaga: mingw-w64, gcc, make, nasm, pkg-config, curl (Ubuntu: apt install mingw-w64 gcc nasm pkg-config make curl).
#   zbuduj-ffmpeg.sh <folder roboczy> <folder wynikowy>
set -euo pipefail
WORK="$(realpath -m "${1:?folder roboczy}")"
OUT="$(realpath -m "${2:?folder wynikowy}")"
FFMPEG=7.1.2 OPUS=1.5.2 LAME=3.100
HOST=x86_64-w64-mingw32
PREFIX="$WORK/prefix"
mkdir -p "$WORK/src" "$PREFIX" "$OUT"
cd "$WORK/src"
fetch() { [[ -f "$2" ]] || curl -fsSL -o "$2" "$1"; }
fetch https://ffmpeg.org/releases/ffmpeg-$FFMPEG.tar.xz ffmpeg.tar.xz
fetch https://downloads.xiph.org/releases/opus/opus-$OPUS.tar.gz opus.tar.gz
fetch https://downloads.sourceforge.net/project/lame/lame/$LAME/lame-$LAME.tar.gz lame.tar.gz
J=$(nproc)

if [[ ! -f "$PREFIX/lib/libopus.a" ]]; then
  rm -rf opus-$OPUS && tar xzf opus.tar.gz && cd opus-$OPUS
  ./configure --host=$HOST --prefix="$PREFIX" --enable-static --disable-shared --disable-doc --disable-extra-programs >/dev/null
  make -j$J >/dev/null && make install >/dev/null && cd ..
fi
if [[ ! -f "$PREFIX/lib/libmp3lame.a" ]]; then
  rm -rf lame-$LAME && tar xzf lame.tar.gz && cd lame-$LAME
  ./configure --host=$HOST --prefix="$PREFIX" --enable-static --disable-shared --disable-frontend --disable-decoder >/dev/null
  make -j$J >/dev/null && make install >/dev/null && cd ..
fi

rm -rf ffmpeg-$FFMPEG && tar xJf ffmpeg.tar.xz && cd ffmpeg-$FFMPEG
PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig" PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig" ./configure \
  --target-os=mingw32 --arch=x86_64 --cross-prefix=$HOST- --pkg-config=pkg-config --pkg-config-flags=--static \
  --prefix="$WORK/ff" --extra-cflags="-I$PREFIX/include" \
  --extra-ldflags="-L$PREFIX/lib -static-libgcc" \
  --enable-gpl --enable-libopus --enable-libmp3lame --enable-shared --disable-static \
  --disable-ffplay --disable-doc --disable-debug --disable-avdevice --disable-network --disable-autodetect \
  --disable-hwaccels --enable-w32threads >/dev/null
make -j$J >/dev/null && make install >/dev/null
cp "$WORK/ff/bin/"*.exe "$WORK/ff/bin/"*.dll "$OUT/"
rm -f "$OUT/ffplay.exe"
$HOST-strip "$OUT"/*.exe "$OUT"/*.dll
cp COPYING.GPLv2 "$OUT/ffmpeg-GPL-2.0.txt"
ls -la "$OUT"
