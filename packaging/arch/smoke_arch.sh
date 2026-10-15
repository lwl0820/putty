#!/bin/bash
# 仅在新建的 Arch 容器执行；应用依赖必须由 pacman -U 从包元数据安装。
set -euo pipefail
mkdir -p .tmp/package-smoke/temp .tmp/package-smoke/wayland-runtime
export TMPDIR="$PWD/.tmp/package-smoke/temp"
pacman -Syu --noconfirm --needed python binutils desktop-file-utils xorg-server-xvfb xorg-xauth weston
package="$(python -c 'import json; from pathlib import Path; root=Path(".tmp/packages/arch"); name=json.loads((root/"构建信息.json").read_text())["包文件"]; assert Path(name).name == name; print(root/name)')"
python packaging/arch/smoke_arch.py "$package" --install-arch --platform offscreen
xvfb-run -a python packaging/arch/smoke_arch.py "$package" --install-arch --platform xcb
chmod 700 .tmp/package-smoke/wayland-runtime
export XDG_RUNTIME_DIR="$PWD/.tmp/package-smoke/wayland-runtime"
export WAYLAND_DISPLAY=puttygen-gui-wayland
weston --backend=headless --renderer=pixman --no-config --socket="$WAYLAND_DISPLAY" --idle-time=0 >.tmp/package-smoke/weston.log 2>&1 &
compositor_pid=$!
trap 'kill "$compositor_pid" 2>/dev/null || true' EXIT
for attempt in {1..60}; do
    [ ! -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] || break
    sleep 0.5
done
[ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ] || { cat .tmp/package-smoke/weston.log; exit 1; }
python packaging/arch/smoke_arch.py "$package" --install-arch --platform wayland
