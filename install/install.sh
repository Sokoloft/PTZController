#!/usr/bin/env bash
# Override this repository setting to install from a fork.
# Either OWNER/REPO or https://github.com/OWNER/REPO is accepted.
set -euo pipefail
GITHUB_REPO="${GITHUB_REPO:-Sokoloft/PTZController}"
GITHUB_REF="${GITHUB_REF:-main}"       # branch/tag containing icon.svg and the desktop template
RELEASE_TAG="${RELEASE_TAG:-latest}"   # latest, or an explicit release tag such as v0.1.0

fail() { printf 'PTZ Controller: %s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || fail 'This installer is for Linux.'
[[ -n ${HOME:-} ]] || fail 'HOME is not set.'
case $(uname -m) in
    x86_64|amd64) arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) fail 'Unsupported architecture; publish a matching AppImage first.' ;;
esac
# Publish this stable asset name on each release, or override APPIMAGE_ASSET.
APPIMAGE_ASSET="${APPIMAGE_ASSET:-PTZ_Controller-${arch}.AppImage}"
repo=${GITHUB_REPO#https://github.com/}; repo=${repo%/}; repo=${repo%.git}
[[ $repo =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail 'Set GITHUB_REPO to OWNER/REPO or its GitHub URL.'
[[ $repo != YOUR_USERNAME/* ]] || fail 'Edit GITHUB_REPO at the top of install.sh first.'
[[ $GITHUB_REF =~ ^[A-Za-z0-9_./-]+$ ]] || fail 'Invalid GITHUB_REF.'
[[ $RELEASE_TAG =~ ^[A-Za-z0-9_.-]+$ ]] || fail 'Invalid RELEASE_TAG.'
[[ $APPIMAGE_ASSET =~ ^[A-Za-z0-9_.-]+\.AppImage$ ]] || fail 'Invalid APPIMAGE_ASSET.'

bin_dir="$HOME/.local/bin"
data_dir="${XDG_DATA_HOME:-$HOME/.local/share}"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/ptz-controller"
icon_dir="$data_dir/icons/hicolor/scalable/apps"
applications_dir="$data_dir/applications"
app_path="$bin_dir/ptz-controller.AppImage"
config_path="$config_dir/config.json"
for path in "$app_path" "$config_path" "$data_dir"; do
    [[ $path == /* ]] || fail 'HOME and XDG directory paths must be absolute.'
    [[ $path != *$'\n'* && $path != *$'\r'* && $path != *$'\t'* && $path != *=* ]] || fail 'Unsupported character in installation path.'
done

if command -v curl >/dev/null 2>&1; then
    download() { curl --fail --location --silent --show-error --retry 2 --connect-timeout 15 --max-time 180 --proto '=https' --proto-redir '=https' --output "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
    download() { wget --https-only --tries=3 --timeout=30 -O "$2" "$1"; }
else
    fail 'Install curl or wget, then run this installer again.'
fi

work_dir=$(mktemp -d)
stage_app=''
cleanup() { rm -rf -- "$work_dir"; if [[ -n $stage_app ]]; then rm -f -- "$stage_app"; fi; }
trap cleanup EXIT
if [[ $RELEASE_TAG == latest ]]; then
    release_url="https://github.com/$repo/releases/latest/download"
else
    release_url="https://github.com/$repo/releases/download/$RELEASE_TAG"
fi
raw_url="https://raw.githubusercontent.com/$repo/$GITHUB_REF/install"
printf 'Downloading PTZ Controller (%s) from %s…\n' "$arch" "$repo"
# Stage every download before touching an existing installation.
download "$release_url/$APPIMAGE_ASSET" "$work_dir/app.AppImage" || fail "Cannot download $APPIMAGE_ASSET; publish it on GitHub Releases first."
download "$raw_url/icon.svg" "$work_dir/icon.svg" || fail 'Cannot download icon.svg.'
download "$raw_url/ptz-controller.desktop" "$work_dir/template.desktop" || fail 'Cannot download the desktop launcher template.'
[[ -s $work_dir/app.AppImage && -s $work_dir/icon.svg && -s $work_dir/template.desktop ]] || fail 'A downloaded file is empty.'
magic=$(od -An -tx1 -N4 "$work_dir/app.AppImage" | tr -d ' \n')
[[ $magic == 7f454c46 ]] || fail 'The AppImage download is not a Linux executable.'
grep -q '<svg' "$work_dir/icon.svg" || fail 'The icon download is not an SVG.'
grep -q '^\[Desktop Entry\]$' "$work_dir/template.desktop" || fail 'Invalid desktop launcher template.'
grep -q '^Exec=' "$work_dir/template.desktop" || fail 'Desktop template has no Exec entry.'

# Escape both desktop-file string syntax and Exec's quoted-argument syntax.
# No shell evaluates the installed launcher paths.
desktop_argument() {
    printf '%s' "$1" | sed -e 's/\\/\\\\\\\\/g' -e 's/"/\\\\"/g' -e 's/`/\\\\`/g' -e 's/\$/\\\\$/g' -e 's/%/%%/g'
}
quoted_app=$(desktop_argument "$app_path")
quoted_config=$(desktop_argument "$config_path")
while IFS= read -r line || [[ -n $line ]]; do
    case $line in
        Exec=*) printf 'Exec="%s" --config "%s"\n' "$quoted_app" "$quoted_config" ;;
        *) printf '%s\n' "$line" ;;
    esac
done < "$work_dir/template.desktop" > "$work_dir/ptz-controller.desktop"
if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$work_dir/ptz-controller.desktop"
fi
mkdir -p -- "$bin_dir" "$icon_dir" "$applications_dir" "$config_dir"
stage_app=$(mktemp "$bin_dir/.ptz-controller.AppImage.XXXXXX")
install -m 0755 "$work_dir/app.AppImage" "$stage_app"
install -m 0644 "$work_dir/icon.svg" "$icon_dir/ptz-controller.svg"
install -m 0644 "$work_dir/ptz-controller.desktop" "$applications_dir/ptz-controller.desktop"
mv -f -- "$stage_app" "$app_path"
stage_app=''
# Settings are deliberately never created or overwritten by installation.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$applications_dir" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data_dir/icons/hicolor" >/dev/null 2>&1 || true
fi
printf 'Installed: %s\nLauncher config: %s\nLaunch PTZ Controller from your application menu.\n' "$app_path" "$config_path"
