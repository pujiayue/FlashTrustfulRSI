# FlashTrustfulRSI

可审计的可信 RSI 研究适配层。当前版本 **0.2.0a1（集成开发中）**；正式主实验尚未完成。

模型统一请求 `gpt-5.6-sol`。每次请求保存独立的调用身份、输入、响应、模型名和 usage；跨进程重启重放成功记录。不同试验即使 prompt 相同，也分别计费和留档。未决请求和失败请求停止执行，须核对后恢复。

## 已有真实运行

|Profile|实际范围|当前证据|
|---|---|---|
|EvoPathBench Memory|accumulation 一条完整流，baseline / episodic_memory / state-off，六检查点|105次模型调用；90评估、10训练、18状态记录；原评分器重算一致。decision_interval=24，为canary设置|
|MINJA QA|原生检索和提示、五级淡化链、原生记忆接纳规则、冻结clean/attack双胞胎|25次调用；恶意写入0/1，受害ASR 0/2。使用nutrition数据修正默认触发词无样本的问题|
|AgentDojo DirectAttack|banking/slack/travel/workspace，各首个可注入任务，原生工具和判定器|8条完整轨迹；banking攻击成功，其余三个未成功；每个suite仅一对样本|
|MEXTRA|原生query生成、合成记忆代理、扩充/删除版本、三次输出匹配|19次调用；原公式和大小写修正版并列。原EHR/WebShop环境尚待接入|
|Misevolution service|全部10个公开服务问题，原生固定偏置记忆与无记忆配对|20次调用；动作解析分母单列；未测试在线记忆学习或其他三类路径|
|CIMemories|3 persona ×10次标注、原完整memory、原披露judge和评分器|恢复标签生成中；研究依赖为CC BY-NC 4.0|

这些结果用于验收链路和评分，不能推出统计性的安全提升。13项工作完整清单及缺口见 `configs/catalog.json`。FlashRSI对齐的T1设计包含286个分数格、858个seed格和288个源任务演化job，见 `configs/t1-design-matrix.json`；该文件明确标为设计矩阵，数据哈希和完整配方冻结后才成为可执行的正式实验manifest。

## 安装与离线检查

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/flashtrustfulrsi list
```

原生研究依赖采用固定commit的source模式。运行真实profile前执行：

```bash
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python bootstrap.py
.venv/bin/python scripts/prepare_external.py
```

`bootstrap.py`跳过上游预计算runs，保留源代码、必要数据及许可证。MEXTRA、MINJA在 `external/` 下载，保持独立；数据使用条款分别适用。CIMemories仅适合其许可证允许的非商业研究用途。

## 配置与运行

复制 `configs/provider.example.json` 为 `configs/provider-v02.json`，将endpoint及secret_ref改为本机配置。凭据文件格式见 `configs/credentials.example.json`；在 `.secrets/provider.json` 本地保存并设权限0600。不要提交密钥。

在仓库目录运行：

```bash
.venv/bin/flashtrustfulrsi plan --profile evopath-memory --job-id evopath-canary-001
.venv/bin/flashtrustfulrsi run --profile evopath-memory --job-id evopath-canary-001
.venv/bin/flashtrustfulrsi status
```

`plan`不调用模型。`run`会使用付费API。其他可选profile：`minja-qa`、`agentdojo-direct`、`mextra-synthetic`、`misevolution-service`、`cimemories`。算法参数通过 `python -m flashtrustfulrsi.<模块名> --help` 查看。相同job ID恢复相同实验；改变配置或源代码应使用新ID。

当前canary阶段共用SQLite账本，设300请求/1000万输入token/100万输出token的技术上限。所有请求均入账，关闭隐式重试；上限持久化，不因重启清零。后续正式阶段需独立冻结完整recipe、job预算和manifest，保留历史账本。模型返回名必须匹配；第三方路由的真实后端身份无法由返回字符串独立确认。

## 即插即用的当前边界

公开入口覆盖六个已实现profile，使用配置选择模块和稳定job ID。它们各自使用原生环境或明确标记的合成环境，尚未验证任意组合互换。SHE完整演化、S-Harness、SafeEvolve、Misevolution、SEABench、AgentPoison优化、AgentBreeder和Trusting Trust仍有集成工作；GuardVPO未公开、API模型权重训练不在本轮可执行范围。

生成代码实验需要真实容器隔离测试通过。迁移后的目标服务器为yingboyun_pare，项目位于 `/root/data1/FlashTrustfulRSI`。仅Docker daemon启动成功不构成容器执行验收。

CI只运行离线合同和评分测试，不使用API密钥、不自动发起付费实验。核心原创代码Apache-2.0；外部依赖保持各自许可，见NOTICE和固定版本清单。
