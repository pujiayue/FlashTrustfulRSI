# FlashTrustfulRSI 的 FlashRSI 扩展架构与统一入口

更新于 2026-11-11。当前只允许 DeepSeek 官方 `deepseek-flash` 开发调试，正式实验保持暂停。本说明覆盖最新组合入口；旧交接内容中的历史模型、进度和入口不覆盖本说明。

## 复用边界

本工具库建在 FlashRSI 上。固定版本 `8567c658f52b73ddcc75cb842d9e8f7f88bd1e6f` 原生提供 13 个算法、30 个 Benchmark、7 个 Harness。`composition.native_inventory()` 直接读取该版本 `flashrsi.runner` 的注册对象，没有复制实现或另建同名注册表。上游当前版本数量可能增长，本项目不静默升级。

原生 Algorithm 保留提案、搜索、候选接纳和归档选择；Agent/Harness 保留资源声明及执行；Benchmark 保留加载与评分；Snapshot 保留不可变版本及谱系。新增 TrustSession 观察原生事件，在选择结束后执行冻结父子评估。替换评估器不会修改原生接纳规则。

不需要重写 30 个 Benchmark 或 7 个 Harness。仍需按声明支持的环境检查依赖、数据、模型协议、容器、工具及证据能力。上游支持、扩展可调用、环境可用、真实验收是不同状态。已接通通用适配，不意味着 13×30×7 个组合均实际运行过。可信工作额外需要的任务、标签、记忆或攻击权限仍由该工作的适配器提供。

## 三个主维度与方法角色

| 维度 | 定义 | 例子 |
|---|---|---|
| RSI 更新对象／类型 | 哪类持久资源被更新，可多标签 | Context、Memory、Skill、Code、Tool、Workflow、Model |
| 可信属性 | 要检验的性质 | D1 有效性与稳定性；D2 行为安全；D3 噪声／攻击鲁棒性；D4 隐私与信息流；D5 可追溯可控；D6 评价与接纳 |
| 进化阶段 | 风险在哪一环产生或被观察 | S1 诊断、S2 提案、S3 评估与接纳、S4 部署与跨代继承 |

OPRO、ACE、SICA 是具体算法名，不是资源类型。RI 是关于改进器自身递归提升的证据要求，不作为 S5。T1–T6 是实验包编号，与 D1–D6 不等同。

| 工作角色 | 接到哪里 | 例子与边界 |
|---|---|---|
| RSI 算法 | 原生 Algorithm 或额外算法插件 | OPRO、ACE、SICA、DGM；执行更新，不只是打分 |
| Benchmark／评估协议 | 原生 Benchmark 加可信 Protocol | EvoPath 多代流、SEABench 序列、CIM 情境披露；每套有自己的输入条件 |
| 指标／评估器 | AuditContext → evaluate → 结构化结果 | EM/F1、父子差、回归率、ASR、披露违规；缺少标签时不得输出安全零 |
| 攻击器 | 受控学习输入、检索结果或工具返回 | MINJA、AgentPoison、MEXTRA；需声明权限与预算，不会自动适用所有任务 |
| 防御／接纳器 | 执行钩子、候选门控或资产更新过程 | SHE、SafeCoEvo；改变执行行为或接纳策略，必须单列条件 |
| 安全演化算法 | Algorithm／Worker／Selector | AgentBreeder 的能力—安全联合搜索；同时是 RSI 与保障方法 |

一篇论文可以贡献多个插件。AgentDojo 本身是通用 Agent 安全 Benchmark，需要增加持久更新与后继任务才能研究 RSI；Trusting Trust 提供污染场景和行为见证，可与 SICA/DGM 等演化器组合。不能把每个可信工作都写成一个通用标量指标，也不能假设每个指标都适用于全部 RSI。

## 统一 Python 接口

代码位于 `src/flashtrustfulrsi/composition.py`。

```python
AuditSpec(name: str, benchmark: Mapping)
AuditContext(name, benchmark, before, after, snapshots)
TrustSession(directory, *, evaluators=None)
await session.evolve(native_algorithm, audits, target_factory, benchmark_factory)
await session.audit(initial_snapshot, selected_snapshot,
                    audits, target_factory, benchmark_factory)
```

`native_algorithm` 是 FlashRSI 原生实例，包含已经准备好的 target、worker 和 source benchmark。需要额外环境的 Benchmark 继续使用上游 `flashrsi.runner._prepare` / `_prepare_target` 及 integrations；这些为固定版本私有接口，升级时必须做兼容检查。不得复制一份任务环境或改原生评分器以追求通过。

`target_factory(role)` 每次返回从同一原始 seed 打开的新 Agent，带有适用的环境绑定。TrustSession 先按原生规则加载冻结快照，再收紧为只读，避免只读范围阻止合法的子版本加载。`benchmark_factory(config)` 返回原生 Benchmark。运行期独立 audit 对象在原生选择结束后创建；CLI 预检可以先读取并验证开发输入，预检信息不传给 Worker。

新评估插件提供 `name`、`dimensions`、`stages` 和 `evaluate(context)`。目前默认 `NativeUtilityEvaluator` 从原生轨迹计算各数值指标的父子差、回归数及分母。相同 snapshot ID 单列为重复推理对照，其波动不能解释为更新效果。复杂攻击和门控仍通过准备好的运行协作者注入；当前没有将所有历史独立方法入口自动迁移成一个通用插件。

`ComponentAuditEvaluator` 通过显式 `evidence_mapper(context)` 复用已有四个被动评分组件：SEABench monitor metrics、AgentBreeder bootstrap、Trusting Trust static、AgentPoison ReAct metrics。阈值选择、Pareto选择、记忆接纳、技能编译仍属于各自策略角色，接口会拒绝将它们冒充被动指标。缺少次要指标的轨迹会显示 `missing_metric` 和观察分母，不会静默补零。

```python
from flashtrustfulrsi.composition import TrustSession, AuditSpec

class MySafetyAudit:
    name = "my-safety"
    dimensions = ("D2",)
    stages = ("S4",)
    def evaluate(self, context):
        # 校验自己的标签和适用条件；缺标签时返回不适用或抛出明确错误。
        # 不修改 context.before / after，不写资产，不向优化器反馈留出结果。
        return my_native_scorer(context.before, context.after)

# prepared_algorithm 和工厂来自原生准备流程或对应可信环境适配器。
session = TrustSession(new_output_dir, evaluators=[MySafetyAudit()])
result = await session.evolve(prepared_algorithm, audits,
                              target_factory, benchmark_factory)
```

## 统一 CLI 与开发配置

```sh
flashtrustfulrsi compose inventory
flashtrustfulrsi compose plan --config development.json --root "$PWD"
flashtrustfulrsi compose run --config development.json --root "$PWD" \
  --job-id dev-dsflash-compose-NEW --image sha256:IMMUTABLE_IMAGE_ID
flashtrustfulrsi compose evaluate --config frozen-transfer.json --root "$PWD" \
  --job-id dev-dsflash-compose-NEW-EVAL --image sha256:IMMUTABLE_IMAGE_ID
```

`plan` 不调用 API；读取数据哈希、原生分片和任务身份，检查源数据与审计问题重叠。`run` 执行原生搜索再审计；`evaluate` 只读取指定的初始／选中快照，不创建 Worker 或 Algorithm，不重新选候选。

开发 JSON 使用 `schema=flashtrustfulrsi.compose.v1`、`scope=development_only`、`formal_data_used=false`、`model=deepseek-flash`。字段包括原生 `algorithm`、`harness`、`source`、`audits`、逐路径 `data_sha256` 和技术 `budget`。源任务只允许 train/dev，audit 只允许 test 角色的开发记录。这里的 test 是程序角色，不允许填入正式实验 test 数据。`evaluate` 另外提供 `frozen.seed_dir` 和 `frozen.initial/selected` 的 `path`、`sha256`。

现有有界 CLI runtime 验收范围为 supplied-context MiniAgent 和 Claude Code、文本 Worker 及独立容器 MiniAgent 目录编辑 Worker。其他原生任务环境通过准备好的 Python 协作者接入；不能把 registry inventory 当作这些运行组合的验收证据。

Chat 与 Messages 各有独立持久化预算，配置中的 budget 对每种 transport 生效，Claude 的两种请求上限合计为两倍 requests；没有实习生个人额度或个人账本要求。Key 只在宿主环境变量 `DEEPSEEK_API_KEY`，不会挂载到容器。已创建的 job 目录不重用；当前 compose CLI 不承诺任意半程自动恢复，先对账再开新 attempt。

## 147 负责人已核验入口

SSH 使用 `gaoling_147_vpn` 的本人账户，项目只写：

```sh
cd /new_disk1/jiayue_pu/FlashTrustfulRSI_team_dev/owner/repo
export PYTHONPATH=src:upstream/RUC-NLPIR-FlashRSI-8567c65
PY=../.venv/bin/python
BASE=runs/owner-compose-deploy-20261111
$PY -B -m flashtrustfulrsi compose inventory
$PY -B -m flashtrustfulrsi compose plan --config "$BASE/claude-control.json"
$PY -B -m flashtrustfulrsi compose plan --config "$BASE/mini-ahe-nq-trivia.json"
```

真实重新调试需要输入官方 Key，并使用全新 job ID；不要重复执行已完成 job，也不要把以下指令用于正式实验：

```sh
read -rsp 'DeepSeek official API key: ' DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
CLAUDE_IMAGE=sha256:0ab166a791d55a51d46be2c6f6d2dd7b466d337b0d3155e386ebf7c438da934d
MINI_IMAGE=sha256:656ed16aae0c452a8cb38a04366ad82b1704d46b800b627a65f25623d1c36461
DEBUG_TAG=$(date -u +%Y%m%d-%H%M%S)
$PY -B -m flashtrustfulrsi compose run --config "$BASE/claude-control.json" \
  --job-id "dev-dsflash-compose-claude-$DEBUG_TAG" --image "$CLAUDE_IMAGE"
$PY -B -m flashtrustfulrsi compose evaluate --config "$BASE/mini-ahe-nq-trivia.json" \
  --job-id "dev-dsflash-compose-nq-$DEBUG_TAG" --image "$MINI_IMAGE"
unset DEEPSEEK_API_KEY
```

时间戳生成新 job 标识；同一秒重复运行仍会拒绝目录冲突。Claude 镜像基于原固定镜像增加 SDK 0.2.153；没有改共享基础镜像或实习生环境。容器使用非 root、禁网络、只读根、CPU／内存／PID限制，仅通过本地socket调用官方API。工具白名单 Read/Glob/Grep/Skill；这次没有验收 Claude 的编辑 Worker 或全工具能力。

## 真实验收与交付要求

- `claude-hotpot-10` 使用默认 seed，OPRO 保留初始版本；Hotpot 留出 EM 1/1→1/1，MuSiQue 0/1→0/1。相同快照重复推理的 F1 变化不归因于更新。
- `claude-control-03` 使用明确声明的格式缺陷 seed，经原生 OPRO 接纳子版本；Hotpot 0/1→1/1，MuSiQue 0/1→0/1。父子四次独立留出容器的实际文件哈希逐项匹配对应快照。此前 control-02 的 MuSiQue 为0/1→1/1，两次都保留，不选择最好一次报告。
- `mini-nq-trivia-02` 冻结此前 AHE 在 SearchQA 选出的快照，没有新演化；NQ EM 1/2→1/2，TriviaQA 2/2→2/2。NQ 使用固定 FlashRSI HF 版本的 MRQA dev 记录，原生加载和评分；不是原始 native NQ span schema 或正式300题测试。
- 完整证据含失败 attempts、官方原始请求响应、原生选择、独立容器回执、快照、轨迹、重算。当前批次总53条成功官方回执，0条未决；正式实验0次启动。后续补跑另计。

每个新增可信插件仍需原生 scorer 对齐、适用条件拒绝测试、更新／无更新对照、真实轨迹和至少一次独立重载。攻击失败、无更新、低效用都如实保留。qky 继续 SHE、S-Harness、SafeEvolve harness；wzh 继续 MINJA、AgentPoison、MEXTRA、CIM。两人只改自己的方法文件、测试和配置，通过负责人合并共享 CLI／协议文件；不需要重新开发 FlashRSI 已有 Benchmark/Harness。

## 剩余缺口

本批已完成目录编辑 Worker、Arbor 原生循环、Misevolution Tool/Workflow 与 SEABench 完整公开资产的开发验证，并增加专用协议的统一哈希证据封装。详见 [2026-11-11 负责人交付](OWNER_PREFORMAL_20261111.md)。SEABench 冻结 480 对标注仍外部阻塞；Model 权重训练不纳入 API 首版。其他原生环境的准备按选入的实验矩阵进行，不重写已有 Benchmark。团队开发候选与正式公开发布分开，实习生方法单独验收；正式 T1–T6 和论文统计未启动。

参考：[FlashRSI 框架](https://ruc-nlpir.github.io/FlashRSI/development/framework.html)、[DeepSeek Claude Code](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/)、[官方 Messages 兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)。
