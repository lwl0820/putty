#!/bin/sh
# 保留标准命令入口；实际程序和动态库使用独立安装目录。
exec /opt/puttygen-gui/bin/puttygen-gui "$@"
