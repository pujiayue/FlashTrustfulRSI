# 负责人接口、恢复与数据隔离验收 · 0.4.0a2

此页补充上一批运行验收，修复上一版尚不能称为完整交接的工程缺口。当前仅使用 DeepSeek 官方 `deepseek-flash` 开发；正式 T1–T6 不启动。旧回执、旧数据、旧候选和实习生工作树均不被改写。

## 1. 本轮关闭的开发缺口

|缺口|实现入口|验证|
|---|---|---|
|公开入口可能默认历史第三方传输|`development.py`、CLI `debug list/plan/run`|18 个负责人原生开发协议显式注册；统一 run 对有官方模式的负责人旧 profile 自动启用该模式，不能使用旧 spool/provider/model/endpoint 覆盖；纯离线见证及实习生接口不被误拦。|
|Misevolution 手册参数与公开源码不一致|`misevolution.py`、`misevolution_online.py`|固定记忆 20 次、在线更新 8 次真实官方调用。在线写入两条经验；三条冻结/学后探针均解析，两个未请求退款探针均无违规退款。固定记忆是干预对照，不能叫在线演化。|
|优化已结束、留出中断后只能重新跑|`composition_recovery.py`、`compose inspect-recovery/recover-audit`|真实失败的 control-01 有封存候选和一份已完成初始轨迹；原 6 个回执已结算。复用该角色，仅补另外三个角色，新增 4 次 Messages 调用，0 次优化器/Worker 调用。|
|已完成留出不能供多个被动评价器复用|`cached_audits`、`evaluator_plugins`|两个已有轨迹的再审计作业均 0 API；原生 utility 与 snapshot-provenance 同时输出，角色文件逐字节保持一致。D5 工程溯源检查不被宣传为新论文算法或安全分数。|
|新检出目录缺固定上游准备入口|`bootstrap.py`、`scripts/prepare_flashrsi.py`|1322 个 Git blob 逐一核实并生成 SHA256 锁；147 全新目录从原始 ZIP 准备，再验证通过。已有文件不符即拒绝，不覆盖。|
|只有注册数，没有可执行准备度检查|`preformal.py`|同一真实数据配方完成 13/13 原生构造；固定镜像通过 CPU/内存/PID/只读/非 root 检查及实际依赖导入。其余 30 Benchmark/7 Harness 的注册继承单列，不冒充运行验证。|
|SearchQA 默认调试读取原 test|`searchqa_deepseek_dev.py`、`exposure.py`|新默认固定为 train[0:2]、dev[0:2]、dev[6:8] 留出。历史请求按原题文本或原 ID 审计；可生成保留原 ID 的新未匹配测试副本，不改原 400/200/1400 数据或旧正式配方。|

本轮模型调用为 20＋8＋4＝32；两个缓存再审计不新增调用。代码变更后还执行全量离线测试和新安装验收，具体哈希、测试数量与候选 commit 见同批 `candidate-release.json`。之前 580 回执批次独立保留，不混在本轮的 32 次里。

## 2. 统一开发入口

在 147 负责人目录：

```sh
cd /new_disk1/jiayue_pu/FlashTrustfulRSI_team_dev/owner/repo
PY=../.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src:$PWD/upstream/RUC-NLPIR-FlashRSI-8567c65"
"$PY" -m flashtrustfulrsi debug list
"$PY" -m flashtrustfulrsi debug plan --profile misevolution-online \
  --job-id dev-dsflash-misevolution-online-plan
"$PY" -m flashtrustfulrsi debug plan --profile evopath \
  --job-id dev-dsflash-evopath-plan --first-update-only --debug-requests 128
"$PY" -m flashtrustfulrsi debug plan --profile agentdojo \
  --job-id dev-dsflash-dojo-plan --suite banking
```

`debug` 保留各原生模块参数：SICA 使用 `--pair-id` 和 `--arm`，DGM 调试要求 `--generations 2 --children 1`，AgentBreeder 要求冻结 `--data`。它不是把不同算法改造成同一个优化器。无需再手动选择模型或 base URL；参数不兼容在模型请求之前报错。

真实 run 只把上面 `plan` 改为 `run`，使用新的合法作业 ID，并在自己的 shell 注入 `DEEPSEEK_API_KEY`。不在配置、命令参数或容器里保存 Key。预算及回执按项目调试作业记录，不要求个人额度或个人账本。旧低层模块保留用于解释历史回执，当前开发优先使用新的 `debug` 入口。

## 3. 候选封存后的恢复

```sh
"$PY" -m flashtrustfulrsi compose inspect-recovery \
  --source-run runs/dev-dsflash-compose-claude-control-01
"$PY" -m flashtrustfulrsi compose recover-audit \
  --source-run runs/dev-dsflash-compose-claude-control-01 \
  --job-id dev-dsflash-compose-my-recovery
```

恢复前同时核实：原作业终态；官方模型与 endpoint；没有 pending/transport_unknown；selection 中的父/选中 Snapshot SHA256 和原生 ID；原 seed 的根；开发数据哈希；每份已完成角色的全部 Task 字段、状态与独立原生重评分。原生 Task 的 tuple 元数据经 JSON 数组保存后，只有公共序列化内容完全一致才重新绑定，不能据此修改题目、标签或答案。

恢复不重构 Algorithm 或 Worker；仅为缺失/未完成的留出角色新建推理。已完成角色逐字节复用并写 `audit-reuse.json`，源作业保持只读。新推理使用新的作业身份和账本，原费用/失败仍保留。未选定候选或回执未决时拒绝此入口，不盲目重发旧请求。

对于全量已完成观测，可零调用增加被动审计：

```sh
"$PY" -m flashtrustfulrsi compose recover-audit \
  --source-run runs/dev-dsflash-compose-claude-ahe-edit-01 \
  --job-id dev-dsflash-compose-my-regrade \
  --evaluators native-paired-utility,snapshot-provenance
```

`native-paired-utility` 属于 D1；`snapshot-provenance` 属于 D5，只核实快照、文件变化和同根/直接父代关系。多步祖先链缺少中间版本时不声称链完整；安全结果保持 null。接纳器和更新器不能借此伪装成被动指标。已有 `ComponentAuditEvaluator` 继续支持提供明确证据映射的原生 scorer，缺风险标注时不能套到任意 QA 轨迹上。

## 4. 固定上游准备与零 API 预检

从新源码检出或已安装 wheel 执行：

```sh
"$PY" -m flashtrustfulrsi bootstrap --root .
# 离线 ZIP 可改用 --archive /authorized/path/flashrsi.zip
"$PY" -m flashtrustfulrsi bootstrap --root . --verify-only
"$PY" -m flashtrustfulrsi preformal \
  --config runs/owner-preformal-20261111b/mini-arbor-guided.json \
  --image sha256:656ed16aae0c452a8cb38a04366ad82b1704d46b800b627a65f25623d1c36461 \
  --output runs/my-readiness.json
```

bootstrap 只获取并逐文件校验原始 FlashRSI，不重写 Benchmark/Harness，也不安装系统包；锁文件随 wheel 交付。原仓库 Apache-2.0 许可保留。依赖只安装到自己的环境，或显式复用已授权的依赖路径。模型网关在线状态不可能由零 API 命令证明，必须另外做明确的小请求诊断。

`preformal` 复用实际 run 的数据/原生构造校验，检查哈希、切分、题目重叠及实际目标/Worker 镜像依赖。`selected_recipe_prepared` 只指该配方的执行前准备，不等于科学结果或所有 13×30×7 组合都通过。

## 5. SearchQA 暴露审计与新副本

旧默认开发配方曾把 test[:2] 放在选中候选后的留出。不能因为它不是优化反馈，就继续称整个 test 完全未经开发接触。现在默认留出一律来自独立 dev[6:8]；先前的适配、screen 和 admission-probe 记录按原样保留。

```sh
"$PY" -m flashtrustfulrsi exposure \
  --output runs/my-development-exposure.json \
  --clean-output datasets/my-unmatched-searchqa
```

审计范围为当前 owner 根的官方开发账本以及 owner-live 开发 spool；正式作业和其他主机不混入。pending/失败/已排队请求也按可能暴露保守登记。匹配原题原文或 ID，不声称识别所有改写，因此“未匹配”不是全局未见保证。报告中逐条给出来源与 call_id。

新副本保留原行 ID、题目、上下文和答案；train/dev 原字节不变，只过滤检测到的 test 行。旧源、旧 39 作业计划和旧结果分母完全不改。该副本是新版本的敏感性/未匹配测试资源，正式主表选择完整原 test 还是新资源必须在新配方中明确分母和暴露声明，不能静默沿用旧计数。

## 6. 分工与仍单列的事项

负责人本轮的改动仅限 owner 的接口、适配、测试、数据副本和开发候选。qky 的 SHE/S-Harness/SafeEvolve 与 wzh 的 MINJA/AgentPoison/MEXTRA/CIM 继续独立开发；公共 CLI 由负责人合并，二者现有目录和环境不被覆盖。

SEABench 冻结 480 对推理图/风险标注仍是外部阻塞，公开两条任务不能替代它。权重训练、完整论文代码任务规模、其他原生环境、正式 T1–T6 与统计不是这 32 次接口调试的完成结果。公开开发分支/alpha 也不被称为完整论文复现或最终成熟发行版。
