# SICA 单模型适配：当前边界

2026-11-05：已完成单模型适配，以及真实隔离容器中的原始 Agent.exec、工具链、自身配置文件编辑、候选导出和新容器重执行。原始元改进与归档选择循环也已完成离线接通，详见 SICA_META_EVOLUTION.md；以下验证均为离线测试，不是模型效果实验。

源码版本 `ed8275dca4d3c5dbf77229964351fe9b424797dc`，在 Trusting Trust 发布的修改基础上创建独立 `sica-sol` 副本。原目录不变。`scripts/adapt_sica_provider.py` 在修改前核对源文件哈希，输出可检查的补丁及 JSON 元数据；补丁已通过对原目录的 `git apply --check`。

## 已实现

所有默认角色（含监督、推理、审阅和 JSON 修复）固定为 `gpt-5.6-sol`。保留原生 OpenAI 消息与工具转换；关闭其他提供商的导入、跨模型回退和自动重试。非授权模型、多次重试配置和超过 16384 的输出上限在调用前拒绝。

128000 的上下文值是适配器配置，并非对平台实际上下文容量的验证。原生 TokenCost 的零权重只是无法取得定价时的内部占位，绝不作为实际零费用。后续运行必须禁用原生费用监控，用外部请求账本报告真实 token 与未知计费。

147 上使用项目内独立 `.venv-sica`，39 个依赖轮子的 SHA256 记录在 `configs/locks/sica-offline-wheels.json`。没有修改系统环境。

## 离线契约

```
python -B scripts/check_original_sica.py /path/to/sica-sol
```

输入：适配后的原源码目录。输出：JSON，包含检查项、模拟调用数、真实 API 调用数及限制。脚本通过 MockTransport 生成原 SDK 响应，并禁止 socket connect。

已验证：原 Agent 模块导入、工具调用响应解析、工具结果请求转换、角色模型一致、配置拒绝、传输错误只尝试一次。结果为 3 次模拟 HTTP，0 次真实 API。

分词器初始化使用模拟对象，因为本次非流式契约不执行 token 估计；不代表真实 tokenizer/流式路径已验证。SDK 锁定为本项目已有传输所用的 2.54.0。

## 尚待完成

- 已接通原 MetaImprovementReasoningStructure 与归档选择；后续需开展真实任务和模型实验。
- 扩展负向边界测试与大规模候选支持；基本工具执行、导出、隔离和候选再次执行已验证。
- 对接原始归档/父代选择，明确 YAML 适配循环与原论文实验的差别。
- 运行真实多种子实验，并通过统一插件入口暴露环境构建、输入输出和证据读取。

当前可称为“原始工具链和候选重执行已离线验证”，不能列为完整 SICA 复现、正式 T4 或 RI 结果。根据用户最新指示，当前不恢复任何付费 API 实验。

## 新增：真实容器中的原始工具链与候选重执行

现已执行原始 `Agent.exec`、主代理到 CodingAgent 的委派、OpenFile / OverwriteFile、ExecuteCommand、SubmitAnswer / Complete。测试先修改 SICA 自身 `base_agent/src/config.py` 添加一个夹具标记，再由原始命令工具导入修改后的模块并验证该标记。候选文件通过受限导出接口取回后，在新容器中重复运行原始 Agent 来验证候选。

该闭环共 14 次**模拟**模型调用，0 次真实 API。输入源码哈希不变；相同输入的缓存重放无额外调用。使用真实离线 cl100k_base tokenizer。容器验证了 1 CPU、512 MiB 内存、64 PID、无网络、只读根目录、非 root、capabilities 全部移除和 no-new-privileges。只有推理专用 Unix socket 可访问；没有宿主 Docker socket 或宿主执行回退。

这验证了原始工具链、对自身代码的编辑及候选重新执行接口，**未验证模型自主发现改进或完整演化效果**；原始 MetaImprovementReasoningStructure / 归档选择已在后续离线契约中接通。夹具结果不得放入论文实验数值。

统一安装包入口：

```sh
python -m flashtrustfulrsi contract \
  --source /path/to/sica-sol \
  --image sha256:314a7d90517ad4af577240372a06ef3e586d007bdf76d9e2c4e1622fbd422b3b \
  --output /path/to/new-contract-evidence
```

代码接口：`sica_container.seed_sica(source, destination)` 和 `sica_container.run_sica(agent_dir=..., prompt=..., image=..., provider=..., artifact_dir=..., call_id=..., timeout=...)`。provider 仅需提供 `invoke(protocol, body, request_id=...)`，可注入离线夹具或未来经过授权的账本传输。输出包含原始答案、候选文件、原生日志及费用未知标记。无答案、传输失败、输入变化、容器失败均显式失败。

`build_sica_image.py` 从已有哈希校验 rootfs、项目内依赖与 tokenizer 缓存构建新镜像归档。导入时需保留 `/usr/local/bin` PATH、`PYTHONPATH=/opt/site-packages`、`HOME=/tmp`；初次 PATH 错误的零 API 失败记录保留。代码/日志导出上限为总计各 2 MiB、单文件 512 KiB、1000 文件，进程输出合计 4 MiB，每次容器调用最多 128 次推理请求。这些是当前明确限制，不能据此宣称覆盖论文所有规模。

另有 `python -m flashtrustfulrsi status --root /path/to/project` 可读取归档后的正式实验作业，分别显示正常完成、执行异常和失败。队列状态为最后记录，不等同实时进程存活探测。
