#!/usr/bin/env bash
# mini-pi 全量离线测试（Skill 捆绑脚本示例）
# 路径相对本 Skill 目录解析：scripts/ → release-check → examples → 06-skills → 仓库根
set -u
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
exec python3 "$ROOT/scripts/test.py"
