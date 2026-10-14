#!/bin/sh
# 在 Ubuntu 22.04/24.04 x64 构建机安装依赖；普通用户无需执行此脚本。
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$project_root/.tmp/dependency-temp"
TMPDIR="$project_root/.tmp/dependency-temp"
export TMPDIR
. /etc/os-release
case "${ID}:${VERSION_ID}" in
    ubuntu:22.04|ubuntu:24.04) ;;
    *) echo "仅支持 Ubuntu 22.04 或 24.04 构建环境" >&2; exit 1 ;;
esac
test "$(dpkg --print-architecture)" = amd64
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential ninja-build pkg-config python3 python3-pip ca-certificates \
    curl xz-utils git openssh-client dpkg-dev patchelf file \
    libfontconfig1-dev libfreetype6-dev libharfbuzz-dev libdbus-1-dev \
    libglib2.0-dev libicu-dev libjpeg-dev libpng-dev libssl-dev zlib1g-dev \
    libx11-dev libx11-xcb-dev libxext-dev libxfixes-dev libxi-dev libxrender-dev \
    libxcb1-dev libxcb-cursor-dev libxcb-glx0-dev libxcb-icccm4-dev \
    libxcb-image0-dev libxcb-keysyms1-dev libxcb-randr0-dev libxcb-render0-dev \
    libxcb-render-util0-dev libxcb-shape0-dev libxcb-shm0-dev \
    libxcb-sync-dev libxcb-util-dev libxcb-xfixes0-dev libxcb-xinerama0-dev \
    libxcb-xkb-dev libxkbcommon-dev libxkbcommon-x11-dev \
    libegl1-mesa-dev libgl1-mesa-dev libvulkan-dev \
    libwayland-dev libwayland-bin wayland-protocols \
    xvfb xauth x11-utils weston dbus-x11 desktop-file-utils fonts-noto-cjk
