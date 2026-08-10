#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Bootstrap a completely self-contained Android build toolchain.
#
# The host machine has no JDK, no pip, no cmake and no sudo, so everything is
# installed under $TC (nothing outside that directory is touched):
#
#   $TC/jdk      Temurin JDK 17          (Gradle / Android build tools)
#   $TC/cmake    prebuilt CMake          (some p4a recipes need it)
#   $TC/local    zlib + libtool          (hostpython3 / libffi recipes need them)
#   $TC/venv     CPython 3.11 + buildozer + cython + pillow
#   $TC/env.sh   sourceable environment for every later build step
#
# Idempotent: re-running skips whatever is already in place.
# ---------------------------------------------------------------------------
set -euo pipefail

TC="${TC:-$HOME/.desqueeze-toolchain}"
JOBS="$(nproc)"
mkdir -p "$TC"/{dl,local}
cd "$TC"

log() { echo "[bootstrap $(date +%H:%M:%S)] $*"; }

fetch() { # fetch <url> <dest>
    if [ -s "$2" ]; then log "cached $(basename "$2")"; return; fi
    log "downloading $(basename "$2")"
    curl -fSL --retry 3 --retry-delay 5 -o "$2.part" "$1"
    mv "$2.part" "$2"
}

# --- 1. JDK 17 -------------------------------------------------------------
if [ ! -x "$TC/jdk/bin/javac" ]; then
    fetch "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse" \
          "$TC/dl/jdk17.tar.gz"
    rm -rf "$TC/jdk.tmp" && mkdir -p "$TC/jdk.tmp"
    tar -xzf "$TC/dl/jdk17.tar.gz" -C "$TC/jdk.tmp" --strip-components=1
    rm -rf "$TC/jdk" && mv "$TC/jdk.tmp" "$TC/jdk"
fi
log "JDK: $("$TC/jdk/bin/java" -version 2>&1 | head -1)"

# --- 2. CMake --------------------------------------------------------------
if [ ! -x "$TC/cmake/bin/cmake" ]; then
    CMAKE_URL=$(curl -s https://api.github.com/repos/Kitware/CMake/releases/latest \
        | grep -oE 'https://[^"]*linux-x86_64\.tar\.gz' | head -1)
    fetch "$CMAKE_URL" "$TC/dl/cmake.tar.gz"
    rm -rf "$TC/cmake.tmp" && mkdir -p "$TC/cmake.tmp"
    tar -xzf "$TC/dl/cmake.tar.gz" -C "$TC/cmake.tmp" --strip-components=1
    rm -rf "$TC/cmake" && mv "$TC/cmake.tmp" "$TC/cmake"
fi
log "cmake: $("$TC/cmake/bin/cmake" --version | head -1)"

# --- 3. zlib (headers are required to build hostpython3) -------------------
if [ ! -f "$TC/local/include/zlib.h" ]; then
    fetch "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz" \
          "$TC/dl/zlib.tar.gz"
    rm -rf "$TC/build-zlib" && mkdir -p "$TC/build-zlib"
    tar -xzf "$TC/dl/zlib.tar.gz" -C "$TC/build-zlib" --strip-components=1
    ( cd "$TC/build-zlib" && ./configure --prefix="$TC/local" >/dev/null \
      && make -j"$JOBS" >/dev/null && make install >/dev/null )
    rm -rf "$TC/build-zlib"
fi
log "zlib: $TC/local/include/zlib.h"

# --- 4. libtool (libffi's autoreconf needs libtoolize) ---------------------
if [ ! -x "$TC/local/bin/libtoolize" ]; then
    fetch "https://ftp.gnu.org/gnu/libtool/libtool-2.4.7.tar.gz" "$TC/dl/libtool.tar.gz"
    rm -rf "$TC/build-libtool" && mkdir -p "$TC/build-libtool"
    tar -xzf "$TC/dl/libtool.tar.gz" -C "$TC/build-libtool" --strip-components=1
    ( cd "$TC/build-libtool" && ./configure --prefix="$TC/local" >/dev/null \
      && make -j"$JOBS" >/dev/null && make install >/dev/null )
    rm -rf "$TC/build-libtool"
fi
log "libtool: $("$TC/local/bin/libtool" --version | head -1)"

# --- 5. uv + CPython 3.11 --------------------------------------------------
# python-for-android is not validated against the host's CPython 3.14, and the
# host python has neither pip nor ensurepip, so fetch a standalone 3.11.
export UV_INSTALL_DIR="$TC/uv" XDG_BIN_HOME="$TC/uv" UV_UNMANAGED_INSTALL="$TC/uv"
if [ ! -x "$TC/uv/uv" ]; then
    log "installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
export PATH="$TC/uv:$PATH"
export UV_PYTHON_INSTALL_DIR="$TC/pythons"

if [ ! -x "$TC/venv/bin/python" ]; then
    log "installing CPython 3.11"
    uv python install 3.11
    uv venv --python 3.11 "$TC/venv"
fi
log "python: $("$TC/venv/bin/python" -V)"

# --- 6. buildozer + build deps --------------------------------------------
if ! "$TC/venv/bin/python" -c "import buildozer" 2>/dev/null; then
    log "installing buildozer toolchain into venv"
    VIRTUAL_ENV="$TC/venv" uv pip install --python "$TC/venv/bin/python" \
        "buildozer==1.5.0" "cython==3.0.11" "pillow" "setuptools" "wheel" \
        "sh" "packaging" "colorama" "appdirs" "jinja2" "build" "toml" \
        "pip"   # buildozer shells out to `python -m pip`; uv venvs omit it
fi
log "buildozer: $("$TC/venv/bin/buildozer" --version 2>&1 | tail -1)"

# --- 7. env.sh -------------------------------------------------------------
cat > "$TC/env.sh" <<EOF
# Source this before running buildozer.
export TC="$TC"
export JAVA_HOME="$TC/jdk"
export PATH="$TC/venv/bin:$TC/jdk/bin:$TC/cmake/bin:$TC/local/bin:$TC/uv:\$PATH"
export CPATH="$TC/local/include\${CPATH:+:\$CPATH}"
export LIBRARY_PATH="$TC/local/lib\${LIBRARY_PATH:+:\$LIBRARY_PATH}"
export LD_LIBRARY_PATH="$TC/local/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export PKG_CONFIG_PATH="$TC/local/lib/pkgconfig\${PKG_CONFIG_PATH:+:\$PKG_CONFIG_PATH}"
export ACLOCAL_PATH="$TC/local/share/aclocal\${ACLOCAL_PATH:+:\$ACLOCAL_PATH}"
export M4="\$(command -v m4)"
export UV_PYTHON_INSTALL_DIR="$TC/pythons"
EOF
log "wrote $TC/env.sh"
log "BOOTSTRAP COMPLETE"
