# AI Edu 2.0 后端

入口为 /api/v2；本地优先、单用户。apps/web 已接 2.0 UI，旧插件仍是兼容基线。旧 API 默认隔离；不要为运行旧前端而打开隔离回归开关。付费授权通过本地启动开关控制，不读取开发阶段门。

**付费默认分两层（2026-11-15 用户决策）**：桌面「AI Edu 2.0」快捷方式与 `scripts/start-aiedu.ps1` **默认开启付费**（`AIEDU_ALLOW_PAID=1`），可用 `-NoPaid` 退回禁付费；底层 `scripts/start-backend-v2.ps1` 仍保持默认禁付费的工程安全基线，需显式 `-AllowPaid`。开启付费只表示允许真实调用，不等于已配好私有模型，也不等于通过质量校准或上线验收。

## 环境与启动

Python 3.12，使用仓库外独立环境。已验证的锁文件为 services/api/requirements-offline.lock.txt（文件名沿用历史，不表示只支持离线）。不使用或升级旧仓库环境。

在 github 根目录：

~~~powershell
python -m venv "$env:LOCALAPPDATA\AI Edu 2.0\venv"
& "$env:LOCALAPPDATA\AI Edu 2.0\venv\Scripts\python.exe" -m pip install -r services/api/requirements-offline.lock.txt
./scripts/start-backend-v2.ps1
# 操作者明确允许真实调用时：
./scripts/start-backend-v2.ps1 -AllowPaid
~~~

Windows 若 C: 虚拟化别名安装报 WinError 17，改用新环境的实际解析路径，不改旧环境。默认监听 127.0.0.1:8010；数据/材料/blob/配置在 %LOCALAPPDATA%/AI Edu 2.0，不导入原库、不重启旧服务。

## 桌面一键入口

双击桌面「AI Edu 2.0」即可：后端、前端（3001，复用已构建的 `.next`）、经同源代理的会话/能力/任务列表 API 依次就绪后打开浏览器 `http://127.0.0.1:3001`。后端端口从 `.next/routes-manifest.json` 的真实代理目标读取（通常 8010）；显式 `-BackendPort` 与构建不符时启动失败并记录日志，不启动错误实例。默认付费入口加载 `%LOCALAPPDATA%/AI Edu 2.0/model-settings.json` 与 `model-roles.private.json`；生成、review、blind_solver 任一缺失就拒绝启动。相关脚本：

| 脚本 | 作用 |
|---|---|
| `scripts/start-aiedu.ps1` | 一键启动，默认付费；`-NoPaid` 关闭付费，`-NoBrowser` 只起服务 |
| `scripts/stop-aiedu.ps1` | 只停止由一键脚本启动并记录在 PID 文件中的进程树 |
| `scripts/build-aiedu-icon.ps1` | 从 `设计/品牌视觉/logo定稿/ai-edu-script-edu.svg` 复现 ICO |
| `scripts/create-aiedu-shortcut.py` | 用 `IShellLinkW` 重建桌面快捷方式 |

日志与 PID 在 `%LOCALAPPDATA%/AI Edu 2.0/launcher/`（工作区外，不进 Git）。图标 ICO 在私有根目录 `ai-edu.ico`。脚本持有独占启动锁；已监听时核验身份和 v2 任务 API 后复用，重复点击不重启。**启动器不停止既有实例**（包括整合轮的隔离实例）。付费开关不作用于被复用实例，当前 v2 能力与参数不符时拒绝报启动成功；模型配置状态读取 v2 角色能力，不能用旧 `/api/health` 的 demo 字段代替。默认付费启动必须具备三个必需角色；显式 `-NoPaid` 可进入禁付费模式。

前端 `/api/*` 的目标在 `npm run build` 时冻结，单独设置 `next start` 的 `API_BACKEND_URL` 无效。改端口须设置该变量后重新构建；桌面快捷方式不再传过期端口参数。`stop-aiedu.ps1` 只回收 PID 文件中命令路径和端口均匹配的自有进程及所有后代，包含 Windows venv 的实际解释器；停止有运行中任务的实例前应先处理任务。启动回归可运行 `python -m pytest scripts/tests/test_desktop_launcher.py -q`（仅本地 HTTP fixture，无数据库/模型调用）。

模型私有文件：model-settings.json；附加角色文件：model-roles.private.json。2026-11-16用户明确选择六个角色全部沿用主生成`deepseek-generation`（deepseek-flash / thinking enabled / high），理解/规划/修复/知识梳理也沿用主生成。前端保存的角色与同ID完整profile优先，私有附加文件只补未配置的默认值，不混合不同端点的凭据。当前旧私有角色覆盖已经备份后清空，分配只维护在model-settings.json。API不返回密钥。同模型/同族不能冒充跨族独立验证；独立检查保留MODEL_FAMILY_NOT_INDEPENDENT缺口，不静默改接GLM/GPT。历史调用身份和unknown回执不随配置改变。HTTP网关不提供TLS传输保护；别名上游身份为操作者声明，容量注册的保守包络不是网关最大容量认证。模型容量须有来源/版本，未知时拒绝。私有根目录和显式模型配置先解析为真实路径，避免Windows跨盘别名导致原子替换失败。

GPT推理输出预留按[官方Chat参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)发送max_completion_tokens；DeepSeek/GLM保持各自max_tokens。实际参数名和值均参与request_hash，提供方拒绝时不自动换参数重试；发送high/max不等于已经独立验证网关内部执行强度。

## 接入顺序

1. 允许的本地 HTTP Origin 显式 POST /api/v2/session，接收 HttpOnly cookie 与 csrf_token。
2. 写请求携带 Origin、X-CSRF-Token、Idempotency-Key。自然语言一次启动用 POST /task-starts，提供 raw_input；mode=configured 需要开启付费，无配置不会偷偷转 demo。需要演示须显式 mode=demo。
3. 无冲突自动理解、选配规范、冻结计划、生成、检查和有界修复；真实需求冲突进入 awaiting_input。保留 21 学科、原始分支字符串、六 scene 和旧 exam 别名。
4. 轮询 /tasks/{id}/snapshot，支持 ETag/304；pending/running、质量未知、模型/阶段/使用量、停止原因分别保留。
5. pause 用当前 state_revision；resume 用 checkpoint_hash 和 revision。unknown 不自动重发；显式新尝试须确认可能重复计费。总调用/token/金额/时间为 null，不取消单次资源限制。
6. 按固定 version 创建 export-set，再按 manifest 下载。下载不触发生成；Markdown/ZIP 字节和 hash 固定，WikiLink 转相对文件或文字，主动 HTML/不安全链接降级。

材料流程：创建 task → 以原始字节 POST /tasks/{id}/materials（X-Filename、X-Expected-Revision）→ PATCH input 选择返回的 source_id → 确认/启动。上传对象不会自动代表用户选择。支持 UTF-8 txt/md/csv、带文字层 PDF、纯文本 DOCX、EPUB；扫描/OCR、旧 DOC/PPT、非文本对象明确拒绝，不以空文本冒充完整。解析需工作区外 resource-policy.json；本机实测包络和错误/取消/背压结果见交接证据，不承诺无限容量。

2026-11-17 整合入口：真实生成默认启用 `execution_policy.structured_arrangement=true`、`task_adaptive_format=true` 和 `quality_loop=true`，策略 `aiedu.repair.v2` 默认每问题6次、8轮、调用总数不限（`root_call_budget:null`）；取消、unknown隔离、lease、单次容量、无进展和防退化仍生效。既有80次等有限授权不静默清零，须在原root使用带CAS、幂等回执和确认的`budget_amendment`；无产物版本的失败/暂停/中断root绑定`checkpoint_hash`，已有版本仍绑定version/content/quality hash。研究、A理解、B生成/联网续写、C复核和优化共用祖先账本，不新建完整任务绕限。上传分支完整保留原始需求、scope和执行策略。材料研究另有明确可见开关，只发公共主题查询，不外发私有原文；金额未知不等于免费。

A先消费完整自然语言、冻结材料及同一profile/rubric，任务自适应路径由A决定分类与布局、每组数量/形式/目标难度和模板适用性，冻结output_design交B/C/整改/阅读与导出共同消费；原规范和来源bytes不改。仅legacy冻结路径仍作原layout匹配与一次校正。普通worker完整C后接入C优化skill→B整题/整单元/关联单元候选→完整复验，固定完成版recheck不重新生成。候选引入逐维负面、降低已有诊断锚点或丢失satisfied观察时拒绝覆盖最佳草稿；这是一道保守的候选保护，不把模型观察升级为confirmed事实。无进展、轮次不足或缺独立外证时保留草稿和具体停止原因，不宣称最终质量通过。demo与旧任务保持原冻结语义，不因升级自动收费；显式replan须确认新生成、保留旧版本/回执/祖先账务。

下载按规范将全部章节合入一份主Markdown；只有冻结制品角色确有独立答案时另给一份答案投影，不按章节拆文件。最终交付门同时要求完整制品、必需检查settled且显式pass、无阻断、profile有效及既定校准资格；其它版本只能显式下载有警示的草稿。预览和重复下载不发模型调用。动作回执not_found仍表示结果未知，不能解释为未执行或放开重复提交。

中文文件名使用 `X-Filename-UTF8: encodeURIComponent(filename)`（原 X-Filename ASCII 合同保留）；服务端解码一次再做路径/扩展名检查。`capabilities.materials.resource_policy_status` 为 ready/unavailable，reason只暴露错误码，不暴露私有策略路径。ready只表示策略有效，实际磁盘/解析仍可能失败。

版本目录：`GET /tasks/{task_id}/versions?artifact_id=...&limit=100&before_version=...`，按版本倒序，返回 `next_before_version`；校验artifact确属task，GET不触发模型。任务列表demo为true/false/null（未运行），进度分母未定义时不伪造百分比。

## 既有规范继承

用户自定义规范不等于内置seed。`POST /format-spec-imports`接收完整records与canonical source_hash；保留ID/版本/字段/时间戳，只插入，同ID不同内容整批409，完全一致无副本；owner/CSRF/幂等回执与其它写入口相同。私有规范原文不进代码库。无default/builtin时可绑定该scene唯一允许选择的自定义规范，多候选返回SPEC_SELECTION_REQUIRED。

匹配继承模板的练习使用`exercise.chapter.v1`适配：有正文的Markdown章标题确定单元，容量chunks不冒充章节；题型/题面/内部完整推理同item，展示按章内连续题号、三段栏目及章末答案。基础/变式等只展示答案，拔高/综合额外展示1—3行解析；另行答案文件只是同item投影。源小节声明覆盖不是已验证语义覆盖。旧HTML复选框明确降级为安全GFM；不允许主动HTML。

匹配阅读模板的`reading.chapter.v1`先生成逐章，再生成全书认知地图/阅读路线，后两节只取各章概要/论证摘要并保留来源hash、缺失/截断标记；展示顺序另冻结，全书层在前。无章材料回退单节；无法安全识别的章节/单章超过已知容量须诚实处理，不造章节或静默丢材料。结构验证不等同阅读忠实性/专业认证。真实源规范同步结果与当次验收见工作区整合交接。

## 能力与诚实出口

- 跨模型族盲检查、C3/C4 换序、包装层定位/hash 校验、风险复核/弃权；无证据不确认，无 finding 不等于正确。
- 有界整数多项式恒等式、字面引文归属和结构/覆盖为可用子集。专业事实、证明、完整数学/教学评价没有 oracle 时仍 unknown。PS5.1/其他目标运行时无经验证隔离执行器，不执行宿主脚本。
- 旧任务兼容的自动修复仍限经证据确认且有本地契约的局部patch与3次/5轮默认；显式新闭环支持版本化策略下的整题/整单元/关联单元候选及全量复验。候选能力不等于专业正确性认证，不能绕过根预算、CAS、unknown或最终交付门。
- 规范/模型配置有受控 v2 适配；知识分析同 root、盲解先行、外评不改、原理只提候选。指导默认关闭，embedding 可选优化未接付费旁路。
- 受控合成基准只覆盖三类确定性适配；语义基准需候选/精炼/检查/独立面板角色满足分离。新建 sealed 与开发/修复切片分组隔离且只消费一次；不自动读取任何既有 gold/holdout。
- ai_synthetic_validated ≠ calibrated。当前不签优秀、DEV-G5、学习收益、真实 PS 执行或生产切换。
- SQLite WAL/FULL、raw SQL/bulk/ORM 同事务 delta、全资产 scratch 备份恢复和幂等回放已具实现；生产恢复需要独立明确授权和真实资产演练。

## 验证与交接

services/api 下运行 python -m pytest tests -q；conftest 在导入应用前冻结隔离库与假配置。仓库根运行 python scripts/generate_v2_contracts.py、python scripts/arch_map.py、node scripts/verify_spec_preview_sync.mjs；生成文件不手改。

工作区 设计/交接/sol6.1 保存复跑脚本、五份交接文档与未完成边界；评测/运行记录 为只追加证据。不要用测试数量、三种子运行数量或未校准模型共识替代综合专业验收。
