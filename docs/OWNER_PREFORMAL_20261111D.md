# 负责人开发候选 0.4.0a3：冻结配方与真实验收

本页是 0.4.0a2 接口说明的增量。开发一律使用 DeepSeek 官方 `deepseek-flash`；不启动正式 T1–T6。这里的“开发验收”是指定小规模配置的链路、状态、评分和交接验收，不代表完整论文复现、完整实验矩阵或防护有效。

## 架构与接入位置

```text
冻结 JSON 配方 → recipes.prepare（0 API：输入/代码/原生 plan 冻结）
             → recipes.run（独占锁、同配方预检、一次执行、超时界限）
               ├─ debug.run → 原生方法专属入口 → 官方 DeepSeekProvider
               └─ compose.run → FlashRSI 原生 Algorithm/Benchmark/Harness
                                      ↓
                     原生结果 + 完整轨迹 + 物理调用回执
                                      ↓
               终态核对 → 原生独立重评分 → evidence envelope → HTML
```

FlashRSI 固定上游继续提供 13 个 Algorithm、30 个 Benchmark、7 个 Harness 的注册和实现，不重新开发这些适配器。`TrustSession` 在原生选择/接纳外接审计；专属安全任务保留其原生输入、评分和运行流程。注册继承不等于所有组合均通过运行。

`debug list` 现有 **19 个协议入口**，其中包含同一论文的不同干预/恢复协议，不能计为 19 种 RSI 算法。此轮 19 个入口均做了实际 CLI 加载检查；新增配方的真实执行验收覆盖 Misevolution online、AgentDojo 静态防护、AgentDojo 记忆链、Trusting Trust YAML 四种配置。其余入口沿用已有独立验收，不能把 help 检查当作真实小跑。

## 本轮关闭项与真实结果

|开发缺口|修复|真实验证|
|---|---|---|
|开发命令尚不能冻结并重复读取|`recipes.py` 和 `recipes prepare/run/inspect`|配置、包代码、输入文件/目录、原生 plan 冻结；已有回执只读返回；失败或未决作业不自动重发。|
|AgentDojo followup 仍依赖旧第三方路径|`dojo_followup.py` 接官方 Provider；统一开发入口自动开启|4 个原生静态防护对照全部正常结束；15 调用。无防护/有防护攻击都成功，4 臂效用均 False。如实保留负面结果。|
|持久化链缺少实际重载和撤回证据|原生 AgentDojo 下游任务 + 项目记忆保留适配器|5 臂：空父代、仅来源、来源+派生、删除来源但保留派生、完整回滚。第二次链从 `states.json` 重载，11 调用；5 臂效用 True、攻击成功 False；API 实际记忆与落盘哈希逐项一致。|
|Trusting Trust 统一参数与底层不一致|增加 `--deepseek-debug` 官方模式别名，统一入口拒绝 provider-mode 覆盖|15 调用；原生 YAML 单元测试及行为见证落盘；这是透明 Context 更新基线，不冒充 DGM/SICA/HyperAgents 全论文复现。|
|目录数据无法冻结，超时只杀直接子进程|目录成员/内容哈希；本次子进程组独立会话和限时终止|新增契约测试；容器/已发出 HTTP 请求仍须依据回执核对，不能凭超时声称远端没有执行。|
|模块版本与发行版本不同步|同步 `__version__` 与包元数据|在全新 147 wheel 环境、空工作目录核实。|

上述五个最初完成作业共 **60** 个新官方物理调用：记忆 v1 11 + 记忆 v2 11 + 静态 15 + Misevolution 8 + Trusting Trust 15。旧攻击来源轨迹只作溯源，不重复计入。最终发行包的独立小跑如有新增，单独列入同批验收 JSON，不静默改变这五项记录。

原生 AgentDojo 是攻击/防护评测框架，**不是 RSI 优化算法**。五臂中的来源提取和经验写入是本项目适配器，必须在论文中写明；source_removed 保留派生资产是显式实验条件，不宣称完成隐私遗忘。rollback 资产恢复相同也不保证独立随机推理输出相同。

## 可直接执行的入口

147 负责人既有开发目录：

```sh
cd /new_disk1/jiayue_pu/FlashTrustfulRSI_team_dev/owner/repo
PY=../.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PWD/src:$PWD/upstream/RUC-NLPIR-FlashRSI-8567c65"
"$PY" -m flashtrustfulrsi debug list
"$PY" -m flashtrustfulrsi debug plan --profile trusting-trust \
  --job-id dev-dsflash-trusting-trust-myplan \
  --image sha256:656ed16aae0c452a8cb38a04366ad82b1704d46b800b627a65f25623d1c36461
```

团队复用冻结开发分支时，克隆到自己的**新目录**，不能用 owner 源目录或覆盖 qky/wzh 已有工作树。建议新发行分支 `owner/preformal-20261111d`；实际 commit、wheel 哈希、147 干净环境和验证回执见同批 `candidate-release.json`。统一 CLI 的公共修改由负责人合并，实习生只提交自己模块和测试。

最小新配方（保存为 `my-online.json`，把作业 ID 改成自己这次唯一的名字）：

```json
{
  "schema": "flashtrustfulrsi.recipe.v1",
  "scope": "development_only",
  "model": "deepseek-flash",
  "formal_data_used": false,
  "profile": "misevolution-online",
  "max_wall_seconds": 1800,
  "parameters": {"job_id": "dev-dsflash-misevolution-mydebug"}
}
```

```sh
# prepare 不需要 Key，不发模型请求；输出已经存在时拒绝覆盖。
"$PY" -m flashtrustfulrsi recipes prepare --root . \
  --recipe my-online.json --output runs/my-online-prepared.json
# 只有实际 run 才需要在当前 shell 注入官方 Key；不要写入 JSON、脚本或容器。
read -r -s -p 'DeepSeek official key: ' DEEPSEEK_API_KEY; printf '\n'
export DEEPSEEK_API_KEY
"$PY" -m flashtrustfulrsi recipes run --prepared runs/my-online-prepared.json
unset DEEPSEEK_API_KEY
# 同一完成配方再次 run：只读 receipt，idempotent_read=true、new_model_requests=0。
"$PY" -m flashtrustfulrsi recipes run --prepared runs/my-online-prepared.json
"$PY" -m flashtrustfulrsi recipes inspect --prepared runs/my-online-prepared.json
```

以上密钥输入命令用于 147 Bash。本地 zsh 请使用自己的安全输入方式，不将 Key 作为命令参数。项目保留作业级执行上限和回执以支持故障核对，不需要个人额度/个人账本。

另外三份实跑配方在 `examples/recipes/`。记忆链的 `source_trace` 必须是本人有权读取的已完成 AgentDojo 原始攻击轨迹，不能拿结果表替代；prepare 会冻结其文件哈希。source_trace 不存在则预检拒绝。目录型 `data` 会冻结目录成员及全部文件内容；新建文件也使旧配方失效。固定 Docker 镜像必须使用完整 sha256 ID。

## 接口约束和恢复

- 配方字段 `parameters` 直接映射原生标量命令参数，布尔 True 为开关、False 不传入。`compose` 也可作为 profile；不重写优化器选择逻辑。
- 禁止自选 model/provider/base-url/endpoint/spool/provider-mode。模型与官方地址由 dispatcher 和 Provider 双重固定。
- `prepare` 冻结整个当时 Python 包的 `.py` 哈希；随后改代码应准备**新配置与新作业 ID**。不能修改已准备文件绕开检查。跨主机路径也应重新 prepare。
- `run` 前再校验输入/代码/原生 plan；本次执行有文件锁。`receipt.json` 只有原生 status/result 都 completed、官方回执全部 settled success、无超时、代码不变、退出码为 0 才给 `accepted_development_completion=true`。
- 完成回执只读复核原生结果与调用数，不重新调用方法；已有原生目录但无本配方回执时拒绝自动重跑。超时、pending、transport_unknown 先核对原日志和调用回执，再用专属恢复入口。
- 原生调用/评分失败不记安全。AgentDojo 保存原生请求与角色/文本规范化后请求，禁止修改评测目标；原生 retry 装饰器仅在单条 episode 内停用，结束后恢复。
- 正式实验配方不允许进入此 launcher。将来正式预算、重复种子、测试分母、模型和统计需独立冻结，不把开发数据直接升级为主表。

验收文件：`runs/<job>/manifest.json,status.json,result.json`、完整原生 trace、持久化 state/write-event、原始/规范化请求、`provider/deepseek-debug.sqlite3`；配方控制位于 `runs/recipe-control/<job>/`。对外提交只给脱敏摘要，完整内部轨迹按授权保留，不提交真实 Key。

## 术语和剩余边界

三个分类轴是 **更新表面、可信维度、进化阶段**。更新表面：Context/Memory/Skill/Tool/Code/Workflow/Model；D1 更新效用与可靠性，D2 行为安全与对齐，D3 噪声/攻击鲁棒性，D4 系统安全与隐私，D5 溯源与可控性，D6 评价器/接纳可信性。S1–S4 在工具库中的操作映射为经验/候选形成、更新构造、评价接纳、持久化与后续使用；这是工程归并，不能冒充综述原文逐字定义。RI 单列为“改进器自身的递归改进”，需要同初始状态、同预算对照；普通记忆更新不能自动称 RI。

T1–T6 是实验区块编号，必须查对应冻结实验计划；不可只因数字相同就等同 D1–D6。可信“方法”包含被动 scorer/benchmark、主动攻击或防护、更新接纳器及 RSI 优化算法，集成时分别注明角色；不能把通用 QA 分数称为安全分数。

仍单列：SEABench 官方冻结 480 图/标注为外部阻塞；模型权重训练、原论文完整代码任务规模和未选中的运行环境不能从这些小跑推出完成。qky/wzh 的模块独立验收；正式数据隔离声明、匹配预算、多种子、主表和论文统计尚未执行。SearchQA 已识别的两题历史开发暴露及 1398 未匹配副本仍沿用 a2 的明确披露。
