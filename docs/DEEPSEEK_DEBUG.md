# DeepSeek Flash 开发调试入口

团队开发基线包含 `flashtrustfulrsi deepseek-debug`。正式历史实验继续使用各自固定的模型、传输与账本。

```sh
python -m flashtrustfulrsi deepseek-debug check
```

`check` 不访问网络，也不写账本。它只显示配置、默认预算和环境中是否有 Key。使用受控环境变量 `DEEPSEEK_API_KEY` 向运行进程注入官方 Key。每人使用自己的项目调试额度。以下命令在允许真实调用后执行。

```sh
umask 077
PROBE_DIR="$PWD/runs/dev-dsflash-preflight-01"
python -m flashtrustfulrsi deepseek-debug preflight --live \
  --job-id dev-dsflash-preflight-01 --run-dir "$PROBE_DIR"
python -m flashtrustfulrsi deepseek-debug status --run-dir "$PROBE_DIR"
```

预检最多四次调用，覆盖纯文本、JSON、函数工具请求、同一个工具调用 ID 的结果回传。成功响应按相同 call ID 和请求哈希回放。若计划要变更，使用新的运行目录。若出现结果不明的请求，查看状态并保留原账本，再由负责人复核后决定下一次尝试。

在开发方法入口注入以下接口。计划阶段单独建立 `DebugBudget` 并把预算参数与 job_id 写入 manifest。

```python
from pathlib import Path
from flashtrustfulrsi.deepseek_debug import DebugBudget, DeepSeekProvider

provider = DeepSeekProvider(
    Path("runs/dev-dsflash-method-01/provider"),
    "dev-dsflash-method-01",
    DebugBudget(requests=16, input_tokens=240_000,
                output_tokens=16_384, per_call_output=2048),
)

# 由已授权的真实作业调用，plan 和离线测试使用注入的测试夹具。
result = provider.invoke("chat", {
    "model": provider.model,
    "messages": [{"role": "user", "content": "Return the requested assessment."}],
    "max_tokens": 256,
}, request_id="seed42-case001-clean-target-turn01")
```

`complete_sync(messages, max_tokens=..., request_id=...)` 和异步 `complete` 也可使用。支持非流式、文本型 Chat Completions 和函数工具。默认显式关闭 thinking。需要 thinking 的任务应在其计划中固定该选项，并依官方要求保留工具调用轮次的历史字段。生成代码和外部工具的执行仍需原有隔离边界。历史容器 socket 和方法入口存在旧模型限制，使用容器的新增适配应明确接入新传输，不能把旧入口的参数直接替换为 Flash。 [DeepSeek Chat API](https://api-docs.deepseek.com/api/create-chat-completion/) · [工具调用](https://api-docs.deepseek.com/guides/tool_calls/)

账本在私有运行目录中的 `deepseek-debug.sqlite3`，同一目录只绑定一个作业和一份预算。请求计数在网络调用前原子预留。输入按序列化请求字节数加 4096 作保守预留，输出按 `max_tokens` 预留，返回后用供应商报告量结算。缺失用量和不明结果保留预留。预算是本地准入限制，实际输入分词和收费由供应商决定。每个请求使用固定官方 HTTPS 地址，禁用环境代理、重定向和 SDK 重试。账本含私有请求和响应，`status` 只输出请求标识、计数和状态。日志及对外摘要不要拷贝私有账本。

## SICA G2 原生容器调试

在包含已核验恢复清单、冻结 G2 候选和只读归档的负责人项目内运行。加 `--plan-only` 可先检查哈希和预算，预检无需凭据。实际运行用进程环境注入官方 Key。

```sh
PYTHONPATH=src ../.venv/bin/python -B -m flashtrustfulrsi.sica_deepseek_debug \
  --review configs/recovery/sica-meta-09-committed-recovery-review.json \
  --job-id dev-dsflash-sica-g2-01 --requests 24 --plan-only
```

调试适配器在容器外将原生 Chat 请求映射为 `deepseek-flash`，关闭 thinking，单次输出上限为 4,096 tokens。原工具及文本消息保留，异步工具通知沿用已有的配对整理逻辑。去掉 `--plan-only` 后，调试产物保存到项目私有 `runs/<job-id>`。其状态报告可提取请求哈希、响应状态和工具名。运行可停在预算边界，候选只保存在该调试目录。正式评分、留出和归档晋升均由各自原入口执行。
