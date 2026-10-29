#!/usr/bin/env bash
set -Eeuo pipefail
trap 'printf "Build failed at line %s. See the error above.\n" "$LINENO" >&2' ERR

# Resolve paths from this script, even when launched from another folder.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
project_dir=$PWD
if [[ $(uname -s) != Linux ]]; then
  echo 'AppImages must be built on Linux.' >&2
  exit 1
fi
case $(uname -m) in
  x86_64) architecture=x86_64 ;;
  aarch64|arm64) architecture=aarch64 ;;
  *) echo 'Supported build architectures: x86_64 and aarch64.' >&2; exit 1 ;;
esac

# Override with PYTHON=/absolute/path/to/python for a dedicated build environment.
if [[ -n ${PYTHON:-} ]]; then
  python_bin=$PYTHON
elif [[ -n ${VIRTUAL_ENV:-} ]]; then
  python_bin="$VIRTUAL_ENV/bin/python"
elif [[ -x .venv-build/bin/python ]]; then
  python_bin="$project_dir/.venv-build/bin/python"
else
  python_bin="$project_dir/.venv/bin/python"
fi
if [[ ! -x "$python_bin" ]]; then
  echo 'Create a virtual environment and install requirements.txt and pyinstaller first.' >&2
  exit 1
fi
if ! "$python_bin" -c 'import PyInstaller, flask, webview, pygame, visca_over_ip, PyQt6.QtWebEngineWidgets, PyQt6.QtWebChannel' >/dev/null; then
  printf 'Install build dependencies with:\n  %q -m pip install -r requirements.txt pyinstaller\n' "$python_bin" >&2
  exit 1
fi

# Prefer an explicitly supplied or installed tool; otherwise cache the official
# release locally. Extract-and-run avoids requiring FUSE on the build machine.
if [[ -n ${APPIMAGETOOL:-} ]]; then
  appimage_tool=$(command -v -- "$APPIMAGETOOL")
elif command -v appimagetool >/dev/null 2>&1; then
  appimage_tool=$(command -v appimagetool)
else
  tool_version=1.9.1
  appimage_tool="$project_dir/build/tools/appimagetool-$tool_version-$architecture.AppImage"
  case $architecture in
    x86_64) tool_sha256=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0 ;;
    aarch64) tool_sha256=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158 ;;
  esac
  mkdir -p build/tools
  if [[ ! -f "$appimage_tool" ]]; then
    tool_url="https://github.com/AppImage/appimagetool/releases/download/$tool_version/appimagetool-$architecture.AppImage"
    printf 'Downloading appimagetool %s (%s)…\n' "$tool_version" "$architecture"
    if command -v curl >/dev/null 2>&1; then
      curl --fail --location --retry 3 --connect-timeout 20 --max-time 300 \
        "$tool_url" --output "$appimage_tool.part"
    elif command -v wget >/dev/null 2>&1; then
      wget --timeout=30 --tries=3 -O "$appimage_tool.part" "$tool_url"
    else
      echo 'Install curl or wget to download appimagetool.' >&2
      exit 1
    fi
    printf '%s  %s\n' "$tool_sha256" "$appimage_tool.part" | sha256sum --check
    mv -- "$appimage_tool.part" "$appimage_tool"
  fi
  printf '%s  %s\n' "$tool_sha256" "$appimage_tool" | sha256sum --check
  chmod +x "$appimage_tool"
fi

"$python_bin" -m PyInstaller \
  --clean --noconfirm --onedir \
  --name PTZ_Controller \
  --paths src \
  --add-data "src/ptz_controller/templates:ptz_controller/templates" \
  --add-data "src/ptz_controller/static:ptz_controller/static" \
  --collect-data webview \
  --hidden-import webview.platforms.qt \
  --hidden-import PyQt6.QtWebEngineWidgets \
  --hidden-import PyQt6.QtWebChannel \
  --hidden-import pygame._sdl2.controller \
  src/main.py
# Only generated staging files are removed; cached tools survive rebuilds.
rm -rf -- "$project_dir/AppDir"
mkdir -p AppDir/usr
cp -a dist/PTZ_Controller AppDir/usr/
cp install/icon.svg AppDir/ptz-controller.svg

sed 's|^Exec=.*|Exec=PTZ_Controller|' \
  install/ptz-controller.desktop > AppDir/ptz-controller.desktop

cat > AppDir/AppRun <<'EOF'
#!/bin/sh
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$app_dir/usr/PTZ_Controller/PTZ_Controller" "$@"
EOF

chmod +x AppDir/AppRun
AppDir/AppRun --version

output="$project_dir/PTZ_Controller-$architecture.AppImage"
# A failed build preserves the previous release.
ARCH="$architecture" APPIMAGE_EXTRACT_AND_RUN=1 "$appimage_tool" AppDir "$output.part"
chmod +x "$output.part"
APPIMAGE_EXTRACT_AND_RUN=1 "$output.part" --version
mv -f -- "$output.part" "$output"
printf '\nBuilt: %s\n' "$output"
