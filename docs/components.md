# 原生组件与容器适配边界

这些组件调用固定 commit、SHA-256 校验后的原始算法代码。它们完成了组件集成和合同样例检查，不代表完整论文算法已复现，也不计入模型实跑或主实验格子的完成数。

|入口|实际复用|输入 → 输出|尚未实现／验证|
|---|---|---|---|
|`seabench-monitor-metrics`|SEABench `progression_features.metrics`|冻结预测、标签和三类surface → 混淆矩阵、recall、FPR|原始推理图标注、monitor训练和完整自进化环境|
|`agentbreeder-bootstrap`|AgentBreeder三种bootstrap统计函数|评分列表、seed → bootstrap均值的中位数及95%区间|scaffold生成、选择、迭代与SALAD端到端实验|
|`safecoevo-memory`|S-Harness原生Memory/Permission stores|带验证标记的经验、query → 准入结果和检索结果|验证标记的来源核验、Guard及在线演化；GuardVPO尚未公开|
|`safeevolve-skills`|SafeEvolve原生compiler/retrieval|已有SkillBank、任务元数据 → runtime bank、选中skills|SkillBank学习、policy SFT/GRPO；API模型不提供权重训练|
|`trusting-trust-static`|原生`classify_patch`|文本diff → added-line eval分类|静态启发式不是漏洞证明；行为witness单列|
|`agentpoison-react-metrics`|AgentPoison原ReAct评分循环|原生answer/gt/traj/retrieval counters → 原ASR和分母|梯度触发优化、embedding模型和真实检索代理|

```bash
python scripts/prepare_components.py
# numpy 来自 requirements.lock；这些命令不调用模型。
flashtrustfulrsi component --component safecoevo-memory \
  --input examples/components/safecoevo-memory.json --output /tmp/memory-check.json
```

`examples/components/`都是人工编写的合同样例。不要将样例输出作为论文实验数据。所有返回值含`paper_experiment_completed: false`；没有标签、分母为零和缺少surface时，优先报错，不能补成安全分数。AgentPoison原ASR-a分母为零时的输出惯例保留，但显式输出分母；原ASR-t实际是1−accuracy。

SHE另有真实运行入口`she-dojo-seed`：加载四种原生seed资产，通过原生SHERuntimeAdapter接入AgentDojo的context/model/tool/final钩子。使用原生deterministic selective judge。没有擅自把工具名改写为SHE训练工具，因而覆盖范围需要另外验证。这个profile测试静态harness，不测试SHE完整演化。

## Docker执行契约

`DockerSandbox`要求不可变image ID/digest，禁网、只读rootfs、非root用户、移除capabilities、no-new-privileges、CPU/内存/PID上限、运行时限，并在超时后清理项目容器。`doctor`执行容器内断言；daemon可连接不足以通过。它目前只支持独立离线任务，不承担联网LLM代理环境的部署。

```bash
flashtrustfulrsi doctor --docker-host unix:///path/to/project.sock --image sha256:IMAGE_ID
flashtrustfulrsi plan --profile trusting-trust-yaml --job-id yaml-001 \
  --input-dir examples/witness --image sha256:IMAGE_ID
flashtrustfulrsi run --profile trusting-trust-yaml --job-id yaml-001 \
  --input-dir examples/witness --image sha256:IMAGE_ID
```

`trusting-trust-yaml`已接原生`behavioral_yaml.py --pydir`：只在容器内运行候选代码，宿主机只读取/暂存字节。镜像构建模板见`docker/Dockerfile.witness`，base必须提供固定digest，PyYAML从本地wheel安装。原作者的“safe”标签仅指该组YAML探针，不是任意Python程序安全性证明。

2026-11-05用户指示：如果当前英博云Docker不能运行，依赖它的实验先跳过，继续集成。复验image import仍报`unshare: operation not permitted`。目前witness输出`skipped_environment`，没有宿主机执行降级。SEABench自修改代码、AgentBreeder scaffold、Trusting Trust演化和完整FlashRSI执行环境仍需后续集成/容器验收；不能因为已有容器公共接口就将这些完整pipeline标为完成。

公开证据中的`raw-artifact-hashes.json`校验的是项目存储中的原始文件。公开manifest中的平台endpoint可脱敏，并显式注明`public_redactions`；不能把脱敏后的JSON当作原始文件来核对相同哈希。
