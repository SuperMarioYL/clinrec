<div align="right"><sub><a href="./README.md">English</a>&nbsp;&nbsp;⇄&nbsp;&nbsp;<b>中文</b></sub></div>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
    <img src="./assets/hero-light.svg" width="880" alt="ClinRec — 本地化临床时间线原语">
  </picture>
</p>

<p align="center"><sub>把传真与扫描的 PHI 就地转换为可审查的临床时间线，面向商业 AI agent。</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license"></a>
  <a href="https://github.com/SuperMarioYL/clinrec/releases"><img src="https://img.shields.io/github/v/release/SuperMarioYL/clinrec" alt="release"></a>
  <a href="https://github.com/SuperMarioYL/clinrec/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/clinrec/ci.yml?label=ci" alt="ci"></a>
  <img src="https://img.shields.io/badge/python-3.12-0071E3.svg" alt="python">
  <img src="https://img.shields.io/badge/agent--ready-5E5CE6.svg" alt="agent-facing CLI">
</p>

> **ClinRec 把一整夹传真与扫描的病历就地整理成去重、可审查的临床时间线 —— PHI
> 全程不出本机,每一步抽取都带 sha-256 审计戳。**

<h2><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 架构</h2>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
    <img src="./assets/atlas-light.svg" width="880" alt="ClinRec 架构:传真病历 → ingest(OCR + 去重)→ resolve(medspaCy NER + 本地化 linker)→ timeline 组装,全程 sha-256 审计链,PHI 不出本机">
  </picture>
</p>

只有两个进程:`clinrec` CLI/TUI 与本地 [Ollama](https://ollama.com) 守护进程。没有微服务、没有云调用、没有 Kubernetes。这个原语真正新的两块是 **本地化 LLM linker** —— 零云调用把杂乱的 OCR 片段归一成编码实体;以及 **逐操作审计链** —— 让审查方仅凭本机 sha-256 产物就能复现每一次抽取步骤。

## 为什么需要它

医院、医保、医疗法律团队的 AI 工程师都在各自重写同一套入库层:把传真和扫描的病历 PDF 还原成临床准确、可审查的时间线,同时 PHI 不出本机。今天失败的那个动词是 *resolve* —— 跨重复传真、自由文本就诊、部分结构化字段做实体解析,汇成一条时间线。具体塌掉的环节:临床工程师把几千页传真病历丢给托管 RAG 栈,要么触发 HIPAA 违规(PHI 出站),要么拿到一个没有审计链、没有去重、没法复现的答案。

ClinRec 提供面向 agent 的 CLI 接口,接到你的 agent 流水线里,让临床 AI 工程师跑一个本地原语,而不是再在内部重写这一层 —— 临床时间线抽取领域里最接近 [affaan-m/ECC](https://github.com/affaan-m/ECC) 的东西。这是一个质量门槛可证伪的 AI 原语,不是黑盒:`clinrec eval` 在去标识 n2c2 数据集上报告本地 NER+linker 的精确率/召回率,对比 medspaCy-only 基线,达不到这条线计划就砍掉。

## 目录

- [架构](#架构)
- [为什么需要它](#为什么需要它)
- [安装](#安装)
- [快速开始](#快速开始)
- [用法](#用法)
- [演示](#演示)
- [配置](#配置)
- [对比相邻工具](#对比相邻工具)
- [定价 — ClinRec Pro](#定价--clinrec-pro)
- [路线图](#路线图)
- [许可证](#许可证)
- [分享](#分享)

<h2><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 安装</h2>

```bash
# 推荐 Python 3.12 + uv(pip 亦可)
uv tool install clinrec
ollama pull llama3.1:8b-instruct      # 一次性、免值守的模型拉取
```

<h2><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 快速开始</h2>

```bash
clinrec init                           # 检测 Ollama、加载 medspaCy、写 ./clinrec.toml
clinrec ingest ./sample-records        # OCR + 去重 + NER + 链接 + 组装一条时间线
clinrec timeline                       # Rich TUI:浏览去重后的事件与审计链
```

<details><summary>示例输出</summary>

```
                      Ingest summary
┌──────────────────────────┬─────────────────────────────┐
│ records                  │ 5                           │
│ duplicates skipped       │ 1                           │
│ entities (coded)         │ 85                          │
│ timeline events (de-dup) │ 37                          │
│ audit entries            │ 128                         │
│ phi_egress               │ False (primitive invariant) │
│ NER engine               │ medspaCy                    │
│ linker engine            │ rule-based (ollama down)    │
│ state                    │ .clinrec/state.json         │
└──────────────────────────┴─────────────────────────────┘
```
</details>

<h2><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 用法</h2>

五个最常用工作流。完整参考:`clinrec --help`。

```bash
# 1. 入库一夹传真/扫描病历(PDF/TIFF/PNG/TXT)
clinrec ingest ./faxes --patient patient-deid-001

# 2. 浏览组装好的时间线(Rich TUI):挑一个事件钻进它的证据片段,
#    或查看完整审计链
clinrec timeline

# 3. 导出可审查的审计日志(JSON-lines,每个操作一行)
clinrec audit --export audit.jsonl
clinrec audit --show                  # 改为打印到 stdout

# 4. 致命证伪 eval:本地 NER+linker 对比 medspaCy-only 基线
clinrec eval                          # 内置精选 gold 集
clinrec eval --gold tests/gold.jsonl  # 你自己的 gold JSON-lines

# 5. 编程式 API(给 agent 流水线用)
python -c "
from clinrec.timeline import TimelineAssembler
from clinrec.resolve import EntityExtractor
from clinrec.llm import Linker
from clinrec.audit import AuditChain
from clinrec.ingest import ingest_folder

records, dedup = ingest_folder('./sample-records')
asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
timeline = asm.assemble(records, patient_pseudonym='patient-deid-001')
print(f'{len(timeline.events)} 个去重事件;phi_egress 不变式:', asm.audit.verify_invariant())
"
```

每个手敲步骤都在 60 秒内;唯一的等待是模型拉取,而且免值守。

<h2><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 演示</h2>

<p align="center">
  <img src="./assets/demo.gif" width="880" alt="ClinRec 演示:clinrec ingest → timeline(Rich TUI)→ audit --export → eval,去标识 n2c2 样本,单卡 GPU,PHI 不出本机">
</p>

10 分钟 happy path(`docs/demo.tape` 由 `.github/workflows/demo.yml` 渲染):`clinrec init` → `clinrec ingest ./sample-records` → `clinrec timeline`(浏览去重事件、钻进证据片段、查看审计链)→ `clinrec audit --export audit.jsonl` → `clinrec eval --gold tests/gold.jsonl`。单卡 GPU,PHI 不出本机。

<h2><img src="https://api.iconify.design/tabler:adjustments.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 配置</h2>

`clinrec init` 写一份极简的 `./clinrec.toml`(运维自管,不随包发布)。顶层键:

| 键 | 类型 | 默认值 | 含义 |
|---|---|---|---|
| `ollama_host` | str | `http://127.0.0.1:11434` | Ollama 守护进程地址(仅本地化) |
| `ollama_model` | str | `llama3.1:8b-instruct` | 本地化 linker 模型 tag |
| `state_dir` | str | `.clinrec` | 本地工作状态(JSON,可审查) |

Ollama 不可达时,linker 降级为确定性规则编码器,CLI 仍能产出可用且完整审计的时间线 —— 审计链会记录每个编码来自哪条路径(`llm_model_id` 区分 `llama3.1:8b-instruct` 与 `rule-based`)。

## 对比相邻工具

ClinRec 对比 [affaan-m/ECC](https://github.com/affaan-m/ECC)(同赛道的 agentic-eval 评测框架)。老实说,这是互补类别,不是头对头克隆。

| 功能轴 | ClinRec | affaan-m/ECC |
|---|:---:|:---:|
| 本地化 PHI,零云调用 | ✓ | — |
| 临床实体解析(medspaCy NER + 编码 linker) | ✓ | — |
| agent 评测框架 / 跑 agent 基准 | 部分 | ✓ |
| 逐操作 sha-256 审计链(可审查) | ✓ | 部分 |
| 去重后的临床时间线组装 | ✓ | — |

ECC 在评测 agentic coding 上更强;ClinRec 是临床时间线抽取的 agent-facing CLI 接口,带可审查的审计链。

<h2><img src="https://api.iconify.design/tabler:currency-dollar.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 定价 — ClinRec Pro</h2>

OSS 原语免费。**ClinRec Pro** 是面向医院 IT 的付费档:本地化部署托管 + 监管级审计包。这是 HIPAA 本地化工具的诚实商业化:PHI 永不触达我们的设施,所以我们卖部署 + 证明,不是卖会泄 PHI 的 SaaS。

| 档位 | 你得到什么 | 价格 |
|---|---|---|
| **ClinRec OSS** | 原语、eval 框架、去标识样本 | 免费 |
| **ClinRec Pro — 本地化部署托管** | 单租户部署在你的 VPC / 你的 GPU 机器上(PHI 留在你的环境,绝不到我们这边)+ SOC2 对齐审计导出 + 季度 eval 复跑 + SLA | 约 $2,500/月 每个托管实例(预付约 $24k/年) |
| **ClinRec Pro — 仅审计包** | 校验报告 + SOC2 对齐审计导出 + 季度 eval 复跑,给自托管 OSS 的团队 | 约 $8k/年 |

计费的是部署 + 证明合同 —— PHI 永不触达支付栈。最小"行,这是信用卡"路径:给设计伙伴发冷邮件,在他们去标识样本上走一遍 `clinrec audit --export audit.jsonl`(审计日志是卖给他们的合规/IT 买方的),30 天本地化部署试点在第 31 天转成 $2,500/月。

<h2><img src="https://api.iconify.design/tabler:map-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 路线图</h2>

- [x] **m1 — 入库 + 临床 NER + 本地化 linker。** OCR + 内容哈希去重 + medspaCy NER + 本地化 Llama 3.1 8B linker,跑在去标识 n2c2 样本上,加 `clinrec eval` 致命证伪框架。
- [x] **m2 — 时间线组装 + 审计链 + Rich TUI。** 编码实体 → 带日期的归一事件、逐操作 sha-256 审计链(无 PHI 外泄不变式)、Rich 时间线浏览器、`clinrec audit --export`。这是 v0.1 可发布产物。
- [ ] **m3 — FHIR R4 导出 + 跨机构 MPI + ClinRec Pro 合规模包。** FHIR R4 bundle 导出、跨机构主索引合并、托管部署 + 合规模包。v0.1 之后。
- [ ] 与临床 AI 工程负责人的设计伙伴试点(ViyaMD / Credo Health / SmarterDx / Weave Bio / DrSwarm)。
- [ ] medspaCy 生态集成 —— 把 ClinRec 列为 timeline + audit 配套。

## 许可证

[MIT](./LICENSE)。在 [github.com/SuperMarioYL/clinrec/issues](https://github.com/SuperMarioYL/clinrec/issues) 提 issue 与 PR。

## 分享

```
ClinRec — 本地化原语,把传真 PHI 转成可审查的临床时间线(medspaCy + llama3.1:8b linker,逐操作 sha-256 审计)。一个质量门槛可证伪的 AI 原语,为你的 agent 流水线而建。 https://github.com/SuperMarioYL/clinrec
```

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
