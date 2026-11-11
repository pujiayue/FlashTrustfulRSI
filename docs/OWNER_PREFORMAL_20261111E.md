# 负责人开发补充：原始算法环境与恢复

本补充记录开发配置，不是正式实验结论。全部新模型调试限定官方 DeepSeek `deepseek-flash`。

- 19 个负责人协议在固定 147 资源与各自依赖环境下执行了真实参数的零调用 plan；对应恢复和迁移入口依赖原来源任务，不能推出跨机器可用。SEABench monitor 必须使用其 Python 3.11 checkpoint 环境，不能混入 Python 3.12 method-site；fixture 不是缺失的官方480图/标签。
- Misevolution Tool 增加 session 输入见证以及可选 `--paired-witness-guidance`，在所有三臂加入相同开发接口要求。一次单题调试中初始/重载/无更新原始脚本输出为 False/True/False，三次独立隔离容器复评分一致。未引导的初始轨迹仍不可评，不能填零。该结果不代表浏览器利用率或论文统计。
- SICA 原生两代验证的 verify01 因首代元改进请求上限终止；verify02 完成第一代，但第二代输出截断；verify03 恢复后再次在16384 token截断。发生已记录的终态截断且第一代完整封存时，可经哈希校验在新作业重启第二代；未决请求、未知用量等不允许恢复。verify04 显式使用 `--meta-requests 480 --meta-output-cap 32768`；覆盖原生输出上限是一项记录在案的开发适配，默认仍保持16384。运行结果以对应回执为准。
- 统一配方对 SICA 已提交状态恢复区分 `physical_receipts`（新作业实际回执）和 `reused_source_receipts`（复制来源回执），防止重复计算。先前冻结版本的历史回执保持原样，需按来源关系重新核算。

负责人开发分支 `owner/preformal-20261111e`。公开 main 和各实习生工作树不作为本分支的写入目标。147 运行结果和内部轨迹保存在各自 owner 作业目录；公开代码不含模型密钥。完整原论文代码任务域、未选择的 Benchmark/Harness 组合及正式统计需单列验收。
