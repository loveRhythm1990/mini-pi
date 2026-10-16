---
name: release-check
description: 发布前对 mini-pi 仓库做一次检查：运行全量离线测试并汇总结果。用户要求「发版、检查、验收」时使用。
---

# mini-pi 发布检查

1. 用 bash 运行本 Skill 目录下的脚本 `scripts/check.sh`；
2. 解读脚本输出：任何一章测试失败就停下并原样报告失败信息，不要尝试修复；
3. 全部通过时输出一行汇总：`全部 N 章测试通过`。

这个 Skill 演示了捆绑脚本：`scripts/check.sh` 相对本目录解析，
由模型通过 bash 工具以绝对/相对路径执行——Skill 不引入新的执行机制。
