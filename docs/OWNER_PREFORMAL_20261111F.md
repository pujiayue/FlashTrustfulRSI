# EvoPath 五次更新与 2Wiki 跨基准开发验证

本页记录 147 上的开发验证，所有新模型调用使用 DeepSeek 官方 `deepseek-flash`；不作为正式实验或统计结果。其代码以 `a15871e` 为基线，复用上游原生算法、任务、评分器与本库冻结快照接口。

## EvoPath：三类原生序列

|序列|作业 ID|新成功调用|评价 / 训练 / 状态记录|
|---|---|---:|---|
|accumulation|`dev-dsflash-evopath-full-01`|105|90 / 10 / 18|
|interference|`dev-dsflash-evopath-interference-01`|138|123 / 10 / 18|
|reversal|`dev-dsflash-evopath-reversal-01`|123|108 / 10 / 18|

每条序列均为一个 campaign、五个训练事件、K0–K5，含 baseline、episodic_memory 和 episodic_memory_state_off。通过记录的 task/checkpoint/repeat ID 验证三条件配对；原生 summary 从全部原始记录独立重算一致。三个冻结配方的重复执行均只读已有回执，新增调用为0。原始序列以前已在项目开发中使用，不能称为未见测试；这是半合成市场模拟任务，不是投资建议，也不支持统计显著性结论。

## 2WikiMultiHopQA：既有快照的迁移审计

采用 FlashRSI 原生 `twowiki_multihopqa`、MiniAgent 和本库 `compose evaluate`；加载已有 AHE 初始及选中快照，并未重启优化器。固定 [数据集版本 fe713bf](https://huggingface.co/datasets/framolfese/2WikiMultihopQA/tree/fe713bfbd1afbca1a65246741a75890405d56a3a)，validation parquet 的 SHA256 为 `408e2dbb28edc6c8b9ca3ba0c94d4fc7bf17ffb923766593a3a7f546ab4cba59`。按 `sha256('ftrsi-dev-20261111f:'+id)` 升序选择两条，保存原始行并与全量源数据逐字段比较。两条均已用于开发调用，不能继续记作未经开发接触的正式测试题。

两个快照各两题，4条官方调用全部成功；原生 canonical-answer EM 都为 1/2，F1 都为 0.785714。轨迹重新绑定到原始 Task 并用原生 Benchmark 独立重评分，结果一致。该实现只评价规范答案文本，不包括别名、supporting facts 或 joint 分数。两题和单次推理不足以证明泛化或安全。

## 147 只读核对

```sh
TEAM=/new_disk1/jiayue_pu/FlashTrustfulRSI_team_dev
ROOT="$TEAM/owner/repo"
PY="$TEAM/owner/.venv/bin/python"
RC="$TEAM/owner/preformal-candidate-final-20261111e"
export PYTHONPATH="$RC/src:$TEAM/owner/method-site:$ROOT/upstream/RUC-NLPIR-FlashRSI-8567c65:$ROOT/upstream/HQ-Lin-EvoPathBench-a0a5962"
cd "$ROOT"
for item in evopath-full interference reversal; do
  "$PY" -m flashtrustfulrsi recipes run --prepared "runs/owner-preformal-20261111f/$item-prepared.json"
done
cat runs/owner-preformal-20261111f/evopath-paired-audit.json
cat runs/owner-preformal-20261111f/twowiki-audit.json
```

这些已完成的配方只读回执，`idempotent_read=true` 且 `new_model_requests=0`；不需要 Key。预备文件绑定实际源码、数据及服务器路径，跨机器运行须在自己的新目录重新 prepare，使用新的作业 ID。运行新作业才在私有 shell 中注入官方 Key，不写入仓库、配置或命令行。不能把上述 2Wiki 开发题作为正式未接触数据，也不能根据注册数量声称全部 Benchmark/Harness 组合已验证。

SEABench 的官方冻结480对图与标注仍为外部阻塞；原论文完整代码任务域、所选但未测试的其他原生环境、多种子正式实验和论文统计仍需单独完成。
