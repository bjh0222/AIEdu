# AI Edu 2.0 — 代码交付区

以父目录 AGENTS.md、当前用户决定和设计/最终技术架构及迁移方案.md 为准。

此树由旧仓库受控复制源码，不包含原库、材料、模型配置或密钥；apps/web 已重建为消费 /api/v2 的 2.0 前端，obsidian-plugin 仍为旧兼容基线，不代表真实 Vault 已验收。

后端使用仓库外独立 Python 3.12 环境，见 services/api/README-v2.md；原仓库及其虚拟环境只读。
测试在 services/api 下执行 python -m pytest -q；conftest.py 必须先设置隔离路径。
架构地图由 python scripts/arch_map.py 生成，不手工修改。
公共TS契约由 python scripts/generate_v2_contracts.py 同时生成后端schema与前端lib/v2/contracts.generated.ts；--check同时检查两端。旧阶段地址按next.config.ts只读重定向，不要求恢复GOAL_PHASES与旧阶段页面。
运行数据默认位于本机 LocalAppData/AI Edu 2.0，禁止复制进入 Git。
