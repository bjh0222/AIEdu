---
name: item-quality-review
description: 面向 AI Edu 命题知识库的候选题静态评价技能。默认输入只有题面，部分题附一段人工简单评注；在没有参考答案、评分标准、测试用例和作答数据的条件下，独立完成正确性复核、难度来源、命题质量、核心命题结构与迁移价值分析，输出精简结构化报告供人工复选，不自动入库。适用于数学题、编程题及其他学科题目的单题或批量质检。当用户要求评价这道题、判断题目好不好、题库质检、候选题筛选、命题结构拆解、变式与迁移分析时使用。
agent_created: true
---

# AI Edu 试题评价（v2 · 题干 + 人工评注模式）

## 0. 默认工作条件（每次评价先读）

**默认输入包 = 完整题面 + 部分题的一段人工简单评注。** 参考答案、官方解析、评分标准、选项干扰项、测试数据/checker、作答或提交数据、来源链接，一律按「未」处理，除非用户本次明确提供。

由此确定执行方式：

- 默认进入**静态评价模式**：依靠独立求解、逻辑推导、结构分析完成评价，**不追问、不等待补材料、不因缺少答案或数据而停止分析**。
- 只有当用户显式提供答案/评分标准/测试数据/作答数据时，才升级为**增强模式**，追加对照核验与量化分析（附录 B）。
- 严禁虚构参考答案、作答数据、统计指标、来源、版权状态、运行结果或人工意见。
- 严禁把「缺材料」当成「题目有错」；也严禁把先验判断写成实测结论。
- 默认输出**精简报告**（第 5.1 节）。完整 A–H 模板只在用户明确要求或进入增强模式时启用。
- 附录 A（论文证据链）与附录 B（计算脚本）为**按需资源**：默认流程不要求逐条引用论文，也不要求读取脚本源码。

## 1. 输入契约与人工评注处理

### 1.1 三类输入

| 代号 | 内容 | 状态 | 作用 |
|---|---|---|---|
| T | 完整题面（含公式、图表、选项、约束） | 必需 | 一切判断的基础 |
| N | 人工简单评注（零到一段，如「巧妙地考查…」「偏难」） | 可选 | 命题意图线索 + 待复核的断言 |
| X | 答案、解析、评分标准、测试数据、作答数据、来源 | 默认缺失 | 仅在用户显式提供时启用增强模式 |

题面来自图片或 PDF 时，先转成可读 Markdown；对 OCR、公式转写、题面截断等不确定处标注「转写不确定」并优先回看原图，不得把猜测当作原题。

### 1.2 评注五分类与处理规则

对每条人工评注先分类，再按规则处理：

| 评注类型 | 典型措辞 | 处理 |
|---|---|---|
| 命题意图 / 好题理由 | 「巧妙地考查…」「夯实基础」「综合性强」 | 作为构念线索，用独立解题路径验证是否吻合；**不得因评注夸奖而提高质量判断** |
| 难度断言 | 「偏难」「竞赛级」「送分题」 | 视为待检验假设，与自身先验难度对照；不一致时并列写出两者及分歧原因，不盲从也不刻意反驳 |
| 解题策略 / 提示 | 「用导数定义」「构造辅助函数」 | 先执行 1.3 的二分判定；若随题下发，立即检查答案泄漏与暗示线索 |
| 来源 / 背景 | 「某年竞赛题」「改编自教材」 | 记为**待核实来源**，不得推断原创或可自由使用 |
| 缺陷 / 争议 | 「答案有争议」「条件多余」 | 优先独立复核该项；命中则进入第 2 步硬性缺陷判定，未命中则写明复核过程 |

硬性要求：

- **原文保留**：报告中逐字引用原始评注，不得改写、折叠或用自己的话覆盖。
- **独立复核在先**：先完成第 1–2 步的独立求解与正确性审查，再对照评注；不得顺着评注的结论倒推解题路径。
- **评注不是质量维度**：无评注的题不扣分也不加分，报告写「无人工评注」即可；批量中不得对有评注的题系统性给更高档结论。
- **冲突记录**：评注与独立结论冲突时，列出「评注断言 / 独立结论 / 依据 / 对结论的影响」，交人工裁决，不擅自合并成规则。

### 1.3 评注是否随题下发（强制二分，改变结论）

评价开头必须声明采用的假设及依据：

- **假设 A：评注会与题面一起下发给学生或被测 AI** → 评注属于题面的一部分。检查策略泄漏、答案泄漏、暗示线索、提示过度；命中则在命题质量中明确下调，并建议「评注与题面分离、仅作入库元数据」。
- **假设 B：评注只给评价者 / 作为知识库元数据** → 仅作为命题意图线索，不判泄漏，可用于确认构念。
- 题面未写明时，**默认采用假设 A（保守）**，并在报告里写明「若改为假设 B，结论如何变化」。

> 决策示例（真实题）：题面为 $f(x+y)=f(x)g(y)+f(y)g(x)$、$f(0)=0,g(0)=1,f'(0)=1,g'(0)=0$，证 $f'(x)=g(x)$；评注「巧妙地考察导数的定义，对于夯实基础大有裨益」。独立求解可得差商 $\frac{f(x+h)-f(x)}{h}=f(x)\frac{g(h)-g(0)}{h}+g(x)\frac{f(h)-f(0)}{h}\to g(x)$，命题为真。**假设 A 下**，评注点明「用导数定义拆差商」这一核心策略，构成暗示线索，作考试/评测题面时质量下调；**假设 B 下**，评注与独立路径吻合，用于确认构念，不判缺陷。同一题面，用途不同，结论相反——报告必须写明假设。

### 1.4 证据标签

每条判断标注证据类型，默认标签由输入条件决定：

- **直接证据**：题面、评注原文中明确写出的信息。
- **推导证据**：独立求解、数学推导、逻辑分析、构造反例所得（**静态模式下的主力证据**）。
- **先验判断**：难度等级、区分潜力、适用人群、迁移价值等推测，必须写「先验，未实测」。
- **实证数据**：真实作答、统计指标、程序提交记录（默认模式一律「未测」）。
- **待核实**：当前材料不足以支持结论，列入待补清单而非留白。

## 2. 技能定位与目标

服务于「候选题搜集 → AI 评价 → 人工复选 → 正式入库」流程。评价目标不是只判断能不能用于考试，而是判断是否值得作为命题知识资产保存、以及能教会 AI 什么。

核心任务：

1. 检查题目是否正确、完整、清晰、可解。
2. 分析真实难度及其**形成原因**，而非只贴难度标签。
3. 判断命题质量与教学/测评价值，避免把「难」等同于「好」。
4. 拆解决定思维过程的核心结构与可替换的表面元素，给出失效边界与变式。
5. 判断典型性、代表性、可迁移性，给出入库建议与推荐类型。
6. 给出可追溯证据、置信度与待人工确认事项。

不做的事：不把搜集到等同于值得入库；不把 AI 评价当成用户最终评价；不虚构任何材料；不因缺真实作答数据就停止静态分析；不自动写入或修改正式知识库。

## 3. 评价原则的优先级与样例学习

按以下优先级执行：

1. 用户本次明确给出的评价要求与判断。
2. 用户提供的评价规范、人工分析样例及后续修正。
3. 本 Skill 的默认框架（并标注「通用框架初评」）。
4. 可核实的研究证据（附录 A，按需）。

用户提供人工分析样例时：把样例视为评价标准的示范而非排版模板；归纳其中稳定的维度、判断条件、因果解释、例外与价值取舍；区分「用户明确提出」「从多个样例归纳的暂定原则」「AI 自行补充」；不从单一样例过度推广；样例冲突时列出冲突请求裁决；新题先独立分析再对照样例，不为迎合而硬套；用户纠正后明确指出标准发生了什么变化并采用最新版本。

**注意**：人工评注中的赞语或难度断言属于「用户观点」，优先级低于独立复核结论；只有用户明确纠正评价规则时，才改变本 Skill 的判断规则。

## 4. 静态评价工作流（默认模式）

批量题目先逐题执行，再生成横向比较。

### 第 0 步 · 定位与假设

识别学科、知识领域、题型、目标学习者与用途；信息缺失时采用题面能支持的**最保守假设**并列出，不追问。用途默认「命题知识库收录」；用户说明为考试/教学/AI 出题参考时，按用途调整侧重：

- 知识库收录：代表性、命题结构、可模仿与迁移价值。
- 考试/测评：效度、难度适配、判定可靠性、公平性。
- 教学使用：概念理解、思维训练、脚手架与反馈价值。
- AI 出题参考：可迁移机制、结构不变量、可变条件与失效边界。

同一题可适合一种用途、不适合另一种，不得用单一总分掩盖差异。声明 1.3 的评注假设。

### 第 1 步 · 题面复现与独立求解

先不看任何评注结论，独立求解或构造验证，写出可核查路径：起点（给了什么、要求什么）→ 关键观察（必须发现什么）→ 核心转化（从哪个表示/概念转向哪个）→ 推理链（步骤依赖）→ 终点（如何得到并验证结论）。对每个关键步骤标注所需知识与思维操作。

- **无参考答案时**：自行给出答案、证明或判定方式，标注置信度（高/中/低）与依据；不得写「与参考答案核对：未提供」之类的空占位。
- **无法独立确定答案时**（如依赖缺失图表、规格不完备、需专门领域事实）：写「待补答案/待补题面信息」，列入待补清单，**不得据此判题目错误**。
- 不得仅凭题面长度、计算量、术语数量、知识点数量判断认知层级。
- 存在多解时比较核心思想、门槛与效率，指出最能体现设计意图的一条；不把最复杂解法当作必需解法。

### 第 2 步 · 正确性闸门（硬性）

检查：题面完整性与符号/图形/单位/约束；条件自洽、充分、相容；可解性与答案唯一性；多解、参数解、不同合理解释；歧义、无关阅读负担、暗示线索与答案泄漏（含 1.3 假设 A 下的评注）；选择题选项唯一性与干扰项的错误来源；编程题的输入输出契约、边界、约束、参考实现可行性（静态检查，未执行）。

硬性缺陷处理：

- **确认**无解、条件矛盾、答案错误、关键规格歧义导致无法可靠作答 → 标记「不建议入库（需修订或淘汰）」，停止给出正向入库建议，只列修订方向。
- **仅因材料缺失**无法核验 → 标记「待补材料」，不判题目本身错误。
- 有意设置冗余条件、开放解法或多答案时，先判断其是否服务于明确的命题/教学目的，不机械视为缺陷（如函数方程题中 $f(0)=0,\ g(0)=1$ 可由条件推出，属教学脚手架）。
- 附带评注若点明解题策略，按 1.3 判定；不得把「评注泄漏」与「题面数学错误」混为一谈。

### 第 3 步 · 认知路径与难度来源（重点）

难度与质量分开判断：难题可能设计优秀，也可能只是繁琐、偏僻或条件不清；简单题也可能有很高教学价值。以下作为**内部检查清单**（逐项过一遍，不必逐项输出）：知识前提、知识组合、关键思维障碍、推理结构、迁移要求、计算与操作负担、题面负担（语言/图形/信息提取是否属于目标能力）、目标群体相对性。

输出压缩为：先验难度等级 1–5（写明该等级对应哪类学习者）+ 置信度 + 最主要的 1–3 个难点 + 难度来源分解（知识/思维/迁移/计算/表达负担）+ 哪些变化会显著改变难度 + **「先验难度判断，尚未实测」**声明。

### 第 4 步 · 命题质量与价值

内部检查清单：考查目标与知识价值、思维含量与机械套用捷径、条件设计（必要/充分/精炼/脚手架）、设问设计（层次与提示过度）、综合与联系是否自然、区分潜力（仅先验）、教学/学习价值、原创性与代表性。

输出压缩为：**主要优点 1–3 条、主要缺陷或局限 1–3 条**，每条必须附题面中的具体依据与修订方向。必须回答「好在哪里 / 不好在哪里，依据是什么」；不接受「综合性强」「考查全面」「思维含量高」等无证据空话。

### 第 5 步 · 核心命题结构与迁移（知识库核心产物）

把题目从具体题面抽象为可复用结构：

- **核心命题意图**：希望学习者完成的核心认知活动。
- **radicals（核心结构要素）**：决定主要思维路径、难度或认知要求的条件、关系、约束、设问与推理环节。
- **incidentals（表面元素）**：可替换而通常不改变核心结构的数值、名称、背景、符号、叙述形式。
- **结构不变量**：改变表面元素后必须保留的关系与推理机制。
- **可变因素**：哪些条件/参数/知识组合/设问可改，改后效果如何。
- **失效边界**：哪些改动会破坏关键思想、改变答案性质、导致难度骤变或变成新题型。
- **可迁移方向**：至少 1 个具体变式或同构题，说明保留了什么、改变了什么；不适合迁移时说明原因，不为填表编造。

### 第 6 步 · 评注复核与静态对抗检查

先输出 1.2 的评注复核结果（原文 / 断言 / 独立结论 / 一致或分歧 / 对结论的影响）。再按题型选择静态检查：

- 数学：代入边界值、构造反例、改变参数、交换条件、检验等价表述、特殊情形与多解；可用 GSM-Symbolic 式的「只换数字/名字」「加一条表面相关但逻辑无关的子句」做鲁棒性推演 [E13]。
- 编程：边界输入、极端规模、重复数据、非法输入契约、规格歧义；**未实际运行代码时写「静态检查，未执行」**。
- 选择题：逐项分析干扰项对应的典型错误、选项线索、互斥性与覆盖性。
- 其他学科：事实依据、概念定义、材料真实性、信息充分性、评分可判定性。

查重与来源核验记录**实际检索范围**；未检索就写「未检索」，不得推断「原创」或「可自由使用」。

### 第 7 步 · 结论与人工复选建议

给出四档之一（规则见第 6 节）、推荐收录类型、一句话理由、按性价比排序的待补材料、需人工确认的问题。明确「AI 只提交评价报告与候选建议，正式入库由用户复选后由独立入库流程完成」。

### 静态可判定 / 必须待补 的边界表（关键）

| 仅凭题干 + 评注**必须给出结论** | 仅凭题干 + 评注**不可判定**，标「待补」且不得猜测 |
|---|---|
| 题面完整性与转写置信度；条件自洽/充分/相容；可解性与独立答案或证明；歧义与多解；暗示线索与答案泄漏（含评注，按 1.3）；认知层级与关键思维障碍；先验难度等级与难度来源；命题结构 radicals/incidentals、结构不变量、失效边界；至少 1 个具体变式；评注一致性；入库价值与推荐类型 | 实测难度 p 值、区分度 D、点二列、干扰项效率、DIF、评分者一致性；编程题 TPR/TNR 与 checker 可靠性；真实来源/版权/查重结论；实际区分度与群体公平性；学生典型错误分布 |

## 5. 报告输出规范

### 5.1 默认精简报告（静态模式）

```
# 候选题评价 #<编号>

- 输入：题面[已提供] ｜ 人工评注[有/无] ｜ 答案/解析[未] ｜ 评分标准[未] ｜ 作答/提交数据[未]
- 用途假设：命题知识库收录（默认）｜ 评注随题下发：是/否（依据）
- 学科 / 题型 / 知识点 / 目标学习者：
- A 独立解答：起点 → 关键观察 → 核心转化 → 推理链 → 结论（置信度：高/中/低）
- B 正确性闸门：通过 / 命中缺陷（具体项）/ 待补材料；一句话结论
- C 评注复核：原始评注（逐字）｜ 断言 → 独立结论 → 一致或分歧 ｜ 对结论的影响
- D 难度：先验等级 1–5（对应人群）｜ 置信度 ｜ 1–3 个主要难点 ｜ 来源分解 ｜ 「先验，非实测」
- E 命题质量与价值：优点 1–3 条（各附依据）｜ 缺陷 1–3 条（各附依据与修订方向）
- F 核心结构：radicals ｜ incidentals ｜ 结构不变量 ｜ 可变因素 ｜ 失效边界 ｜ 至少 1 个具体变式
- G 静态对抗检查：实际执行项与结果 ｜ 未执行项（如未运行代码、未检索来源）
- H 结论：收录建议（四档之一）｜ 推荐类型 ｜ 一句话理由 ｜ 待补材料（按性价比排序）｜ 需人工确认的问题
- 评价边界：直接/推导证据、先验判断、不能凭当前材料成立的结论
```

输出预算：单题默认 300–600 字（复杂题可上浮）；批量每道 150–300 字 + 横向汇总。**不得为凑模板堆「未提供/未测」占位**；未测项集中写在「评价边界」一行即可。用户未指定路径时在回复中直接输出；指定路径时写入该文件。

### 5.2 完整 A–H 模板（仅用户要求或增强模式）

```
## A. 基本信息与证据状态
临时编号 / 学科·知识点·题型 / 来源与链接 / 目标学习者与用途 / 证据清单 / 转写不确定性
## B. 题目复述与独立解答
核心要求 / 独立解答路径 / 与参考答案的核对 / 正确性与完整性结论
## C. 难度分析
先验等级 / 置信度 / 主要难点 / 来源分解 / 前置知识 / 难度变化因素 / 实测数据状态
## D. 命题质量与学习价值
考查目标 / 思维含量 / 条件设计 / 设问设计 / 知识联系 / 区分潜力 / 教学价值 / 主要优点 / 主要缺陷
## E. 核心命题结构
命题意图 / radicals / incidentals / 结构不变量 / 可变因素 / 失效边界 / 可迁移构想
## F. 证据与对抗检查
已执行 / 未执行待核验 / 反例·边界·变体结果 / 论文证据（编号与等级）
## G. 知识库建议
收录建议 / 推荐类型 / 值得保存的命题经验 / 需人工确认的问题 / 与已有样本比较的重点
## H. 评价边界
直接证据 / 模型推断 / 不能仅凭当前材料成立的结论
```

### 5.3 批量任务

1. 为每题分配临时编号，保留原始来源与原文；逐题执行核心流程，通用说明可合并但不得省略单题结论与证据。
2. 识别重复题、同构题、近似题与不同结构题；比较**核心结构**而非题面相似度。
3. 输出横向比较：难度分布、命题结构分布、知识点覆盖、样本类型缺口、最值得人工优先复检的题及原因；统计口径写清（候选总数、成功解析数、无法解析数、重复/近似数、完成评价数、各建议档数量、主要样本缺口）。
4. 不为了达到预设数量降低标准；不因某题有人工评注就优先给高档结论。

## 6. 评分、定级与结论规则

- 单项可用 0/1/2 记录：**2** 明确成立且有直接/推导证据；**1** 部分成立或需补充；**0** 明确存在问题；**N/A** 不适用；**待验证** 当前证据不足。评分只作结构化记录，**不做加权平均**，不允许某高分项抵消正确性缺陷。
- 硬性质量闸门：题目或答案存在已确认的实质性错误、条件冲突或无解、关键规格缺失、歧义导致合理答案无法稳定判定、关键图表/公式/题干信息缺失导致无法可靠还原 → 阻止「优先推荐入库」。
- **禁止把「暂缓/待补证据」当默认结论**：只有连独立解答都无法完成（如题面截断、关键图缺失）时才使用「暂缓/待补证据」。缺实测数据、缺评分者一致性、缺来源核验，**不构成暂缓理由**；静态模式下结论应落在「优先推荐入库 / 可考虑入库 / 不建议入库」之一，并标注「静态评价完成，实证证据待补」。
- 四档建议：**优先推荐入库**（正确性已核验，命题结构/难度特征/教学价值有明显代表性）／**可考虑入库**（有价值，但需与现有样本比较或补材料）／**暂缓待补证据**（关键材料不足到无法完成静态分析）／**不建议入库**（实质错误、价值有限、重复度高、不符合本次知识库目标）。
- 推荐收录类型可多选或空：优秀典型题 / 高难度题 / 综合题 / 基础关键题 / 典型缺陷题 / 边界样本 / 其他。
- 报告中必须**分开**给出：先验难度、命题质量、教学/学习价值、知识库收录价值。严禁「难，所以好」「简单，所以没价值」。

## 7. 学科专项（静态模式最低要求）

- **数学**：独立求解；验证条件充分性、必要性、相容性与特殊情况；识别关键概念/定理/方法与推理步骤；判断主要障碍属知识缺口、结构观察、策略选择、推理链还是计算负担；检查更简解法及其是否改变考查目标；分析条件与设问的命题作用；提出具体变式。
- **编程**：输入域、数据类型、取值范围、边界与异常输入约定；输出格式与合法输出形式；时空复杂度要求与参考解可行性；样例是否覆盖关键情况；规格是否存在歧义；题目考查的是目标算法/数据结构还是隐藏规格与语言陷阱。**未运行代码时明确写「静态检查，未执行」**；无提交数据时不得声称已算 TPR/TNR。
- **其他学科**：事实依据、概念定义、材料真实性、题干信息充分性、评分标准能否支持稳定判定。

## 8. 论文证据与量化工具的使用边界

- 附录 A 的论文用于支持评价维度和方法选择，不得把单一论文的样本结果当成普遍阈值；优先选用与当前学科、题型、目标群体、用途匹配的研究；区分同行评审与预印本；引用时写出其支持的具体判断，不堆引用；不把统计相关写成因果。
- p 值、区分度、点二列、DIF、评分者信度、TPR/TNR 等**只在用户提供了真实数据时计算**，否则写「未测」。先验难度来自结构分析，可用于样本分层与决定后续试测优先级。
- 附录 B 脚本仅在增强模式且具备对应 CSV 时启用；禁止用虚构数据填充，禁止在默认模式中读取或运行它。

## 9. 与 AI Edu 命题知识库的数据边界

评价输出是「候选题评价档案」，不是正式知识库记录。候选档案至少保留：原题全文与原始附件、来源信息与获取时间、原始答案/解析及其来源（若提供）、AI 独立解答、**用户原始批注原文（不可被 AI 覆盖或改写）**、AI 结构化评价、评价规范/Skill 版本、证据状态与置信度、待复核项。人工复选后再决定是否抽取为正式母题、题目实例、命题规律或质量记录；不把单题报告中的每句话直接转成 DesignPrinciple，命题规律须经跨样本归纳与人工确认。

## 10. 硬性规则速查

- 默认只有题干（＋可选评注），静态模式一步到位，不追问、不等待、不因缺材料停摆。
- 先独立求解，再对照评注；评注原文逐字保留，评注观点不凌驾于独立结论。
- 必须声明评注是否随题下发；未写明时按「随题下发」保守判定，并写出另一假设下结论如何变化。
- 每条判断带证据标签；先验结论写明「先验，非实测」。
- 缺材料 ≠ 题目错误；缺实测数据 ≠ 暂缓入库。
- 默认输出精简报告；不堆空占位；不合成总分；不自动写入知识库。
- 附录 A/B 按需使用，默认流程不读取脚本源码。

---

## 附录 A · 论文证据链（按需引用）

> 仅在需要为某个判断提供文献依据时读取本附录；默认静态评价不要求逐条引用。
> 引用编号统一写作 [E#]，并在报告中写明该文献支持的具体判断。**A** = 同行评审期刊/会议正式论文；**B** = 预印本、会议短文或新近工作（只作方法参考，不作定论）。检索核实日期：2026-11-06。

### 一、经典测量学与命题规范

- **[E1] A** Haladyna, T. M., Downing, S. M., & Rodriguez, M. C. (2002). *A Review of Multiple-Choice Item-Writing Guidelines for Classroom Assessment*. Applied Measurement in Education, 15(3), 309–333. 以 27 本教材 + 27 项研究验证 31 条命题准则，分为内容、格式、题干、选项四类；核心是消除暗示线索、干扰项取自学生典型错误。https://eric.ed.gov/?id=EJ660246
- **[E2] A** Tarrant, M., Ware, J., & Mohammed, A. M. (2009). *An assessment of functioning and non-functioning distractors in multiple-choice questions*. BMC Medical Education, 9:40. 514 道题、1542 个干扰项，仅 52.2% 有效，10.2% 无人选，仅 13.8% 的题目三个干扰项全有效；有效干扰项越多，题目越难且区分度越高。https://pubmed.ncbi.nlm.nih.gov/19580681/
- **[E3] A/B** Tarrant, A. M., & Ware, J. (2013, AMEE 会议论文). *Cueing effects of item writing flaws in multiple-choice questions*. 3623 道题中含暗示线索缺陷的题显著更容易、区分度更低；建议施测前同行评审。https://hub.hku.hk/handle/10722/195565
- **[E4] A** Abedi, J., & Lord, C. (2001). *The Language Factor in Mathematics Tests*. Applied Measurement in Education, 14(3), 219–234. 1174 名八年级学生；仅降低数学题语言复杂度而不改数学内容即显著提升成绩，ELL 与低 SES 群体受益更多。http://eric.ed.gov/?id=ej636352/
- **[E5] A** Maulina, N., & Novirianthy, R. (2020). *Item Analysis and Peer-Review Evaluation...*. 100 道 MCQ：9 道负区分度、30 道区分度差、42 道四个干扰项全失效；主张项目分析结果回灌命题与同行评审。https://jurnal.ugm.ac.id/jpki/article/download/49006/28263
- **[E6] A** Huntley, A., Engelbrecht, J., & Harding, A. *A Model for Measuring the Quality of a Mathematics Question*. QI 模型三参数＝区分度指数 + 信心指数 + 专家期望；区分提供应答题 PRQ 与建构应答题 CRQ；依据 Romberg 的「数学本质、学习规律、公平」三标准。http://www.pphmj.com/article.php?act=art_download&art_id=5387

### 二、数学题专项

- **[E7] A** Stein, M. K., Smith, M. S., Henningsen, M., & Silver, E. A. (2000). *Implementing Standards-Based Mathematics Instruction*. Teachers College Press. Mathematical Tasks Framework：认知需求四级（记忆／无联结程序／有联结程序／做数学），并指出任务在「设置→实施」中会认知降级。https://files.eric.ed.gov/fulltext/EJ1362838.pdf
- **[E8] A** COACTIV 分类体系（Jordan et al., 2006；应用于数学题质量研究）。数学题 8 维编码：能力水平、情境建模、数学内建模、基本概念、语言复杂度、数学推理、表征使用、技术操作；双人编码 κ≈0.68–0.69。https://maa.tandfonline.com/doi/full/10.1080/14794802.2026.2692608
- **[E9] B** Razavi, P., & Powers, S. (2025/2026). *Estimating Item Difficulty Using Large Language Models and Tree-Based Machine Learning Algorithms*. arXiv:2504.08804. 5170 道 K–5 数学/阅读题；LLM 抽取认知与语言特征 + 树模型预测难度 r 最高 0.87，优于 LLM 直接打分；给出七步落地流程。https://arxiv.org/abs/2504.08804
- **[E10] B** Tong, J. et al. (2026). *ProIQA: A Process-Based Framework for Fine-Grained Math Item Quality Assessment*. arXiv:2609.15292. 质量＝知识概念 + 难度 + 学科核心素养；主张「题干 + 解题过程」双视图（LLM 推理树 + GNN），明确批判只看题干的浅层指标；记录基线 LLM 把冗长题判为 Reasoning、简练几何题判为 Knowing，与专家相反。https://arxiv.org/abs/2609.15292
- **[E11] B** Liang, H. et al. (2025/2026). *MathDebugger: Detecting and Diagnosing Errors in Synthetic Mathematical Data*. arXiv:2502.19058. 2000 正确题 + 2000 错误题（4 类题干错误）+ 2000 解答（610 条错误、3 类）；人工标注 Fleiss κ=0.69–0.91；强推理模型细粒度质检远未饱和，错误类型监督显著提升修复。https://arxiv.org/abs/2502.19058
- **[E12] B** Ballon, M., Algaba, A., Verbeken, B. F., & Ginis, V. (2026). *Benchmarks Saturate When The Model Gets Smarter Than The Judge*. arXiv:2601.19532. 审计 Omni-MATH 4428 道奥数题：647 道（14.6%）题干需修订，247 道（5.6%）为非标准题（需证明/估算/图）；判定器分歧中原 Omni-Judge 错 96.4%。三条审计属性：LaTeX 可编译性、可解性、可验证性。https://arxiv.org/abs/2601.19532
- **[E13] A** Mirzadeh, I. et al. (ICLR 2025). *GSM-Symbolic*. arXiv:2410.05229. 只改数字/名字即造成性能下降与显著方差；加入一条表面相关、逻辑无关的子句最多使性能下降 65%。https://arxiv.org/abs/2410.05229
- **[E14] B** Zhang, H. et al. (Scale AI, 2024). *A Careful Examination of Large Language Model Performance on Grade School Arithmetic*. arXiv:2405.00332. 人工新编 1205 题匹配 GSM8K 难度；部分模型族准确率下降 8–13%，降幅与模型复现 GSM8K 样本的概率正相关。https://arxiv.org/abs/2405.00332
- **[E22] B** *Building Teacher Capacity to Generate and Evaluate Math Items with Small LLMs* (AIMECon 2026 WIP). 六维 rubric（答案键正确性、效度、对齐、认知需求、干扰项诊断性、清晰度）；75 道 LLM 生成题中答案键正确仅 49%，六维全通过仅 9%。https://aclanthology.org/2026.aimecon-wip.44.pdf

### 三、编程题专项

- **[E15] A** Liu, J., Xia, C. S., Wang, Y., & Zhang, L. (2023). *Is Your Code Generated by ChatGPT Really Correct? (EvalPlus)*. arXiv:2305.01210. 原基准平均少于 10 个测试；HumanEval+ 扩充 80× 后 pass@k 最高下降 19.3–28.9% 且模型排名反转；另发现原 HumanEval 18 处（11%）参考解缺陷、docstring 描述不精确。https://arxiv.org/abs/2305.01210
- **[E16] A** Wang, Z., Liu, S., Sun, Y., Ding, M., & Li, H. (2025). *CodeContests+: High-Quality Test Case Generation for Competitive Programming*. Findings of EMNLP 2025, 5576–5600. 把测试用例视为二分类器，用 172 万条真实提交计算 TPR（用例正确性）与 TNR（覆盖度）；原数据集逾 4000 题 TPR≤0.1，重建后合格题数近翻倍，下游 RL 收益显著提升。https://aclanthology.org/2025.findings-emnlp.299/
- **[E17] A** Singhal, S., & Kumar, V. (2023). *Creating Thorough Tests for AI-Generated Code is Hard*. COMPUTE '23, ACM. 即使经 EvalPlus 增强，属性测试仍找出 1913 个通过全部测试的错误解，最差题充分性上界仅 56.3%；三类缺陷：输入空间探索不足、新手式错误、题面规格歧义（如是否计入非字母字符未定义）。https://dl.acm.org/doi/fullHtml/10.1145/3627217.3627238
- **[E18] A** Alkafaween, U., Albluwi, I., & Denny, P. (2025). *Automating Autograding: Large Language Models as Test Suite Generators for Introductory Programming*. J Comput Assist Learn, 41: e13100；arXiv:2411.09261. 26 道 CS1 题、2.5 万+ 提交；LLM 生成的测试套件暴露题面歧义，且少数违反题目约束的用例会误杀大量正解。https://arxiv.org/abs/2411.09261
- **[E19] B** Zhou, S. et al. (2025). *AutoCode: LLMs as Problem Setters for Competitive Programming*. arXiv:2510.12803. 专家五维评分（可解性、解法正确性、质量评级、新颖性、难度）与层级门槛式合成；LLM 直接评质量与专家差距大，但难度/难度增益是较好代理（与质量相关达 0.60）；测试套件与官方判定一致性近 99%。https://arxiv.org/abs/2510.12803
- **[E20] A** Jain, N. et al. (2024). *LiveCodeBench: Holistic and Contamination Free Evaluation of LLMs for Code*. arXiv:2403.07974. 按发布日期持续采集新题以规避污染；发现部分模型 HumanEval 高分却在新题上落后（过拟合簇）。https://arxiv.org/abs/2403.07974
- **[E21] A** Messer, M., Brown, N. C. C., Kölling, M., & Shi, M. (2024). *Automated Grading and Feedback Tools for Programming Education: A Systematic Review*. ACM TOCE, 24(1), Article 10. 系统综述 121 篇（2017–2021）：绝大多数工具只评正确性（单元测试），极少评可维护性/可读性/文档，反馈多止于「通过/失败」。https://eric.ed.gov/?id=EJ1419855

### 主题 → 证据映射（v1 维度编号，仅作索引）

> 下表 D1–D9 是 v1 的九维度编号；v2 已改用静态评价工作流，此表只用于按主题快速定位文献，不表示本技能的评分维度。

| 主题（v1 编号） | 主要依据 |
|---|---|
| D1 效度对齐与认知层级 | E7, E8, E10, E6 |
| D2 科学正确性与可解性 | E11, E12, E15 |
| D3 题面清晰度与反作弊线索 | E1, E3, E4, E17, E18 |
| D4 难度与区分度 | E2, E5, E9, E19 |
| D5 判定体系可靠性 | E12, E15, E16, E17 |
| D6 干扰项与选项诊断性 | E2, E5, E22 |
| D7 公平性与群体不变性 | E4 |
| D8 原创性/污染与鲁棒性 | E13, E14, E20 |
| D9 过程/解题路径证据 | E10, E21 |

### 使用注意

- 不要把 B 级预印本中的具体百分比当作普适常数；它们是特定数据集上的观测值，用于说明「该维度确实会失效」。
- 阈值（尤其 TPR/TNR ≥0.90、p 值区间）是工程建议，须按用途与目标群体调整，并在报告中写明来源。
- 引用时保留编号，避免把「方法结论」误写成「领域定论」。

---

## 附录 B · 量化脚本 item_metrics.py（仅在有真实数据时使用）

> 默认输入（题干 + 评注）下**不会用到本附录**，无需读取或执行。仅当用户提供真实作答 CSV 或编程提交 CSV 时，把下方代码块另存为 `item_metrics.py` 再运行；禁止用虚构数据填充。
> 命令：`python item_metrics.py self-test`；`python item_metrics.py ctt --input responses.csv --output metrics.json`；`python item_metrics.py tpr --input submissions.csv --output tpr.json`。

```python
#!/usr/bin/env python3
"""Compute item-quality metrics from CSV files. No third-party dependencies.

Subcommands:
  ctt        Classical test theory metrics from response data.
  tpr        Test-case quality (TPR/TNR) from submission data.
  self-test  Run built-in assertions on synthetic data.

Examples:
  python item_metrics.py ctt --input responses.csv --output metrics.json
  python item_metrics.py tpr --input submissions.csv --output tpr.json
  python item_metrics.py self-test

ctt CSV columns:
  item_id, student_id, correct (0/1)          required
  total_score (number)                          optional, ability proxy
  chosen_option (string)                       optional, for distractor analysis
  is_key (0/1)                                 optional, marks the keyed option

tpr CSV columns:
  label (1 = correct solution, 0 = buggy solution)        required
  judged_pass (1 = test suite accepted, 0 = rejected)     required
  item_id                                                 optional, groups results
  submission_id                                           optional
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, stdev

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def read_csv(path):
    """Return a list of row dicts, each carrying a _line number for errors."""
    p = Path(path)
    if not p.is_file():
        raise ValueError("input file not found: %s" % path)
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row = {}
            for key, value in raw.items():
                if key is None:
                    continue
                row[key.strip()] = value.strip() if isinstance(value, str) else value
            row["_line"] = line
            rows.append(row)
    if not rows:
        raise ValueError("CSV has no data rows")
    return rows


def as_int(value, field, line, allowed=(0, 1)):
    if value is None or value == "":
        raise ValueError("line %d: column '%s' is empty" % (line, field))
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        raise ValueError("line %d: column '%s' must be 0/1, got %r" % (line, field, value))
    if allowed and number not in allowed:
        raise ValueError("line %d: column '%s' must be one of %s" % (line, field, allowed))
    return number


def as_float(value, field, line):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError("line %d: column '%s' must be a number, got %r" % (line, field, value))


def split_groups(scores, fraction=0.27):
    """Return indices of the lowest/highest ability groups (27% convention)."""
    n = len(scores)
    if n < 6:
        k = max(1, n // 2)
    else:
        k = min(max(3, int(round(n * fraction))), n // 2)
    order = sorted(range(n), key=lambda i: scores[i])
    return order[:k], order[-k:]


def point_biserial(rows):
    ones = [r["ability"] for r in rows if r["correct"] == 1]
    zeros = [r["ability"] for r in rows if r["correct"] == 0]
    if not ones or not zeros or len(rows) < 3:
        return None
    sd = stdev([r["ability"] for r in rows])
    if sd <= 0:
        return None
    p = len(ones) / len(rows)
    return (mean(ones) - mean(zeros)) / sd * math.sqrt(p * (1 - p))


def analyze_distractors(rows, high_idx, low_idx, key):
    high_set, low_set = set(high_idx), set(low_idx)
    by_option = defaultdict(list)
    for idx, row in enumerate(rows):
        if row["chosen_option"]:
            by_option[row["chosen_option"]].append(idx)
    details = []
    nfd_count = 0
    n = len(rows)
    for option in sorted(by_option):
        picked = by_option[option]
        share = len(picked) / n
        high_share = sum(1 for i in picked if i in high_set) / max(1, len(high_idx))
        low_share = sum(1 for i in picked if i in low_set) / max(1, len(low_idx))
        is_distractor = option != key
        reasons = []
        if share < 0.05:
            reasons.append("选择率低于5%")
        if is_distractor and high_share > low_share + 0.02:
            reasons.append("高分组选择率更高")
        if is_distractor and reasons:
            nfd_count += 1
        details.append({
            "option": option,
            "is_key": option == key,
            "is_distractor": is_distractor,
            "share": round(share, 4),
            "high_group_share": round(high_share, 4),
            "low_group_share": round(low_share, 4),
            "non_functioning": bool(is_distractor and reasons),
            "reasons": reasons,
        })
    n_distractors = sum(1 for d in details if d["is_distractor"])
    efficiency = ((n_distractors - nfd_count) / n_distractors) if n_distractors else None
    return details, (round(efficiency, 4) if efficiency is not None else None), key


def compute_ctt(rows):
    parsed = []
    for row in rows:
        line = row.get("_line", 0)
        for required in ("item_id", "student_id", "correct"):
            if required not in row:
                raise ValueError("missing required column '%s'" % required)
        parsed.append({
            "item_id": row["item_id"],
            "student_id": row["student_id"],
            "correct": as_int(row.get("correct"), "correct", line),
            "total_score": as_float(row.get("total_score"), "total_score", line),
            "chosen_option": row.get("chosen_option") or None,
            "is_key": as_int(row.get("is_key"), "is_key", line) if row.get("is_key") not in (None, "") else None,
        })

    notes = []
    has_total = any(d["total_score"] is not None for d in parsed)
    ability = {}
    if has_total:
        bucket = defaultdict(list)
        for d in parsed:
            if d["total_score"] is not None:
                bucket[d["student_id"]].append(d["total_score"])
        ability = {s: mean(v) for s, v in bucket.items()}
        fallback = defaultdict(int)
        for d in parsed:
            fallback[d["student_id"]] += d["correct"]
        for s in {d["student_id"] for d in parsed}:
            if s not in ability:
                ability[s] = float(fallback[s])
        if set(ability) - set(bucket):
            notes.append("部分学生缺少 total_score，已用答对题数作为能力代理")
        proxy = "total_score"
    else:
        bucket = defaultdict(int)
        for d in parsed:
            bucket[d["student_id"]] += d["correct"]
        ability = {s: float(v) for s, v in bucket.items()}
        proxy = "sum_correct"
        notes.append("未提供 total_score，使用答对题数之和作为能力代理")

    by_item = defaultdict(list)
    for d in parsed:
        d["ability"] = ability[d["student_id"]]
        by_item[d["item_id"]].append(d)

    item_ids = sorted(by_item)
    students = sorted(ability)
    matrix = {s: {} for s in students}
    for iid, group in by_item.items():
        for d in group:
            matrix[d["student_id"]][iid] = d["correct"]
    complete = [s for s in students if len(matrix[s]) == len(item_ids)]

    kr20 = None
    if len(item_ids) >= 2 and len(complete) >= 3:
        totals = [sum(matrix[s][i] for i in item_ids) for s in complete]
        variance = stdev(totals) ** 2
        if variance > 0:
            pq = 0.0
            for i in item_ids:
                p_i = mean(matrix[s][i] for s in complete)
                pq += p_i * (1 - p_i)
            kr20 = len(item_ids) / (len(item_ids) - 1) * (1 - pq / variance)
    else:
        notes.append("作答矩阵不完整或题目数不足，跳过 KR20")

    results = []
    for iid in item_ids:
        group = by_item[iid]
        n = len(group)
        p = mean(d["correct"] for d in group)
        low_idx, high_idx = split_groups([d["ability"] for d in group])
        p_low = mean(group[i]["correct"] for i in low_idx)
        p_high = mean(group[i]["correct"] for i in high_idx)
        D = p_high - p_low
        rpb = point_biserial(group)

        key = None
        explicit = [d["chosen_option"] for d in group if d["is_key"] == 1 and d["chosen_option"]]
        if explicit:
            key = Counter(explicit).most_common(1)[0][0]
        else:
            correct_opts = [d["chosen_option"] for d in group if d["correct"] == 1 and d["chosen_option"]]
            if correct_opts:
                key = Counter(correct_opts).most_common(1)[0][0]
        details, efficiency, key = analyze_distractors(group, high_idx, low_idx, key)

        flags = []
        if n < 30:
            flags.append("样本量偏小（n<30），指标仅作参考")
        if p < 0.20:
            flags.append("过难")
        elif p > 0.85:
            flags.append("过易")
        if D < 0:
            flags.append("负区分度，优先检查答案键与题面歧义")
        elif D < 0.15:
            flags.append("区分度不足")
        if rpb is not None and rpb < 0.15:
            flags.append("点二列相关偏低")
        if any(d["non_functioning"] for d in details):
            flags.append("存在无效干扰项")

        results.append({
            "item_id": iid,
            "n": n,
            "p_value": round(p, 4),
            "difficulty_interpretation": "越接近0越难",
            "discrimination_D": round(D, 4),
            "high_group_p": round(p_high, 4),
            "low_group_p": round(p_low, 4),
            "point_biserial": round(rpb, 4) if rpb is not None else None,
            "keyed_option": key,
            "distractor_efficiency": efficiency,
            "distractors": details,
            "flags": flags,
        })

    top_flags = []
    if kr20 is not None and kr20 < 0.80:
        top_flags.append("KR20 低于 0.80，整卷信度偏低")
    return {
        "mode": "ctt",
        "n_items": len(item_ids),
        "n_students": len(students),
        "ability_proxy": proxy,
        "kr20": round(kr20, 4) if kr20 is not None else None,
        "kr20_note": "仅对作答完整的学生计算；完美人造数据可能大于1",
        "top_flags": top_flags,
        "thresholds": {"p": "0.30-0.70", "D": ">=0.30", "point_biserial": ">=0.15",
                       "distractor_share": ">=5%", "KR20": ">=0.80"},
        "items": results,
        "notes": notes,
    }


def compute_tpr(rows):
    parsed = []
    for row in rows:
        line = row.get("_line", 0)
        for required in ("label", "judged_pass"):
            if required not in row:
                raise ValueError("missing required column '%s'" % required)
        parsed.append({
            "item_id": row.get("item_id") or "__all__",
            "label": as_int(row.get("label"), "label", line),
            "judged_pass": as_int(row.get("judged_pass"), "judged_pass", line),
        })
    groups = defaultdict(list)
    for d in parsed:
        groups[d["item_id"]].append(d)

    results = []
    for iid in sorted(groups):
        group = groups[iid]
        tp = sum(1 for d in group if d["label"] == 1 and d["judged_pass"] == 1)
        fn = sum(1 for d in group if d["label"] == 1 and d["judged_pass"] == 0)
        tn = sum(1 for d in group if d["label"] == 0 and d["judged_pass"] == 0)
        fp = sum(1 for d in group if d["label"] == 0 and d["judged_pass"] == 1)
        tpr = tp / (tp + fn) if (tp + fn) else None
        tnr = tn / (tn + fp) if (tn + fp) else None
        accuracy = (tp + tn) / len(group)
        flags = []
        if (tp + fn) < 30 or (tn + fp) < 30:
            flags.append("某类样本不足30条，指标仅作参考")
        if tpr is not None and tpr < 0.90:
            flags.append("TPR 低于 0.90：测试用例可能误杀正解")
        if tnr is not None and tnr < 0.90:
            flags.append("TNR 低于 0.90：测试覆盖不足，未能检出错误解")
        results.append({
            "item_id": iid,
            "n": len(group),
            "n_correct_solutions": tp + fn,
            "n_buggy_solutions": tn + fp,
            "tp": tp, "fn": fn, "tn": tn, "fp": fp,
            "TPR": round(tpr, 4) if tpr is not None else None,
            "TNR": round(tnr, 4) if tnr is not None else None,
            "accuracy": round(accuracy, 4),
            "flags": flags,
        })
    return {
        "mode": "tpr",
        "definition": "TPR = 正解被判通过的比例；TNR = 错误解被判失败的比例",
        "thresholds": {"TPR": ">=0.90", "TNR": ">=0.90"},
        "items": results,
        "notes": ["阈值是工程建议值，按用途调整", "正解/错解标签需来自人工复核或官方判定"],
    }


def run_self_test():
    ctt_rows = []
    for s in range(30):
        high = s < 15
        for iid in ("Q1", "Q2", "Q3", "Q4"):
            if high:
                option = "A"
            elif s == 29:
                option = "D"
            elif s % 2 == 0:
                option = "B"
            else:
                option = "C"
            ctt_rows.append({
                "item_id": iid,
                "student_id": "S%d" % s,
                "correct": 1 if high else 0,
                "total_score": 4 if high else 0,
                "chosen_option": option,
                "is_key": 1 if option == "A" else 0,
            })
    ctt = compute_ctt(ctt_rows)
    q1 = ctt["items"][0]
    assert abs(q1["p_value"] - 0.5) < 1e-9, "p value should be 0.5"
    assert abs(q1["discrimination_D"] - 1.0) < 1e-9, "D should be 1.0"
    assert q1["point_biserial"] is not None and q1["point_biserial"] > 0.8, "point-biserial too low"
    assert any("无效干扰项" in f for f in q1["flags"]), "should flag a non-functioning distractor"
    assert ctt["kr20"] is not None, "KR20 should be computed"

    good = ([{"item_id": "P1", "label": 1, "judged_pass": 1} for _ in range(40)]
            + [{"item_id": "P1", "label": 0, "judged_pass": 0} for _ in range(40)])
    perfect = compute_tpr(good)["items"][0]
    assert perfect["TPR"] == 1.0 and perfect["TNR"] == 1.0, "perfect suite should score 1.0/1.0"

    bad = ([{"item_id": "P2", "label": 1, "judged_pass": 1} for _ in range(40)]
           + [{"item_id": "P2", "label": 0, "judged_pass": 1} for _ in range(40)])
    weak = compute_tpr(bad)["items"][0]
    assert weak["TNR"] == 0.0, "all-pass suite should have TNR 0"
    assert any("TNR" in f for f in weak["flags"]), "weak suite should be flagged"

    print("self-test: PASS")
    print("  ctt  Q1: p=%.2f D=%.2f r_pbis=%.3f KR20=%.3f" % (
        q1["p_value"], q1["discrimination_D"], q1["point_biserial"], ctt["kr20"]))
    print("  tpr  perfect suite: TPR=%.2f TNR=%.2f" % (perfect["TPR"], perfect["TNR"]))
    print("  tpr  weak suite:    TPR=%.2f TNR=%.2f" % (weak["TPR"], weak["TNR"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Item quality metrics (CTT / TPR-TNR)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ctt = sub.add_parser("ctt", help="classical test theory metrics from responses.csv")
    p_ctt.add_argument("--input", required=True)
    p_ctt.add_argument("--output", help="write JSON here instead of stdout")
    p_tpr = sub.add_parser("tpr", help="test-case quality from submissions.csv")
    p_tpr.add_argument("--input", required=True)
    p_tpr.add_argument("--output", help="write JSON here instead of stdout")
    sub.add_parser("self-test", help="run built-in assertions")
    args = parser.parse_args(argv)

    try:
        if args.command == "self-test":
            return run_self_test()
        rows = read_csv(args.input)
        result = compute_ctt(rows) if args.command == "ctt" else compute_tpr(rows)
        result["source"] = str(Path(args.input).resolve())
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
            print("written: %s" % args.output)
        else:
            print(payload)
        return 0
    except (ValueError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

---

## 附录 C · 版本与迁移说明

- **v1**（本工作区早前版本）：六步闸门 + 九维度评分 + V1–V7 一票否决，依赖参考答案、评分标准、测试用例与作答数据；多文件结构（references / assets / scripts）。适用于考试测量场景。
- **v2**（当前版本）：面向 AI Edu 候选题知识库，默认输入收缩为「题面 + 可选的一段人工简单评注」，新增静态评价模式、评注五分类与「评注是否随题下发」二分假设、静态可判定/待补边界表、精简默认报告；论文证据链与计算脚本降级为按需附录。单文件自包含。
- v1 全部文件完整归档于工作区 `dist/backup/`，需要回滚或查询旧阈值、旧示例时从归档中取用，不在技能目录内重复保存。
- 增强模式（用户显式提供答案、评分标准、测试数据或作答数据）下，可恢复 v1 的对照核验与量化分析，但结论仍须按 v2 的报告规范与定级规则输出。

