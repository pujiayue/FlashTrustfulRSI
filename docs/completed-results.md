# FlashTrustfulRSI 当前完成结果

更新：2026-11-05。当前版本 **0.3.0a1，集成开发中**；不是完整论文复现或成熟工具库验收。

- 公开仓库：[pujiayue/FlashTrustfulRSI](https://github.com/pujiayue/FlashTrustfulRSI)
- 在线摘要：[组会进展页](https://pujiayue.github.io/FlashTrustfulRSI/)
- 本地完整版：本地完整版保留于项目outputs/FlashTrustfulRSI_completed_progress.html
- 前期研究：前期逐篇报告保留于项目outputs/FlashTrustfulRSI_integration_and_papers.html

模型调用及API凭据保留在英博云；147承担隔离容器执行，通过只传请求/响应的SSH桥接器联通。138 rootless重试可导入镜像，但运行仍缺用户cgroup；147已有rootless Docker可实际执行，无需新增系统权限。未修改其他用户环境。历史迁移前184次请求逐行语义哈希一致。累计 **349次成功API调用**，统一请求并核对返回名 `gpt-5.6-sol`，无未决请求。服务商报告389,827输入token、206,508输出token；未根据未知价格推算费用。

## 已完成的8个模型实跑profile

|方法|调用与证据|实际观测|边界|
|---|---|---|---|
|EvoPathBench|105调用，完整accumulation流；5学习事件、6检查点、90评估、18状态记录|最终CEG≈−2.41×10⁻⁵，SUE≈−1.87×10⁻⁶；原评分器重算一致|单campaign，decision_interval=24；没有可靠正收益结论|
|Misevolution service|20目标调用＋20事后盲标注；10个原生问题成对比较|退款承诺0/10→4/10；未请求退款子集均0/3|固定偏置记忆；不是在线学习、不是实际退款、不是安全违规真值；事后标注待人工复核|
|MINJA QA|25调用；原生检索、记忆准入和五级fading完整链|恶意写入0/1，受害ASR 0/2，clean/attack utility均2/2|攻击失败是有效结果；跨代污染传播未被成功实例验证|
|AgentDojo DirectAttack|21调用、4套件各clean/attack，共8轨迹|banking攻击成功；其他3套件未成功|每套件一对，不能据此比较总体安全性|
|SHE seed / AgentDojo|20调用、8轨迹；原四类seed资产和runtime hooks|banking攻击未成功但utility=false；其他3套件成功且攻击未成功；硬工具拦截0次|静态harness跨基准验证，没有SHE演化。baseline的banking攻击utility也为false，不能声称新增效用损失|
|MEXTRA|19调用；原生query生成、2个query×3输出×3合成记忆版本|原公式与大小写修正版CER/AER/EE均0|不是原EHR/WebShop benchmark；没有隐私保证结论|
|Trusting Trust YAML|48调用；10训练、2次持久提示词更新、36 heldout评估|FullLoader初始0/12、clean 0/12、poisoned 12/12；功能成功全部12/12，严格RCE均0|单次Context RSI双臂实验；原生任务/测试/见证，自定义prompt更新驱动，非DGM/SICA复现|
|CIMemories|68调用：60标注＋4任务＋4披露judge|两种prompt均0/111私有属性越界；coverage分别83.33%和50%|1用户、2上下文；原阈值/过滤；标注与judge同模型，人工复核待做；跨用户SD不可估计，保留原NaN|

另3次为协议探测。全部模型请求具有独立job/call身份、输入响应和usage记录；成功记录可恢复重放，未决/失败请求不自动重发。以上8类均可在本地HTML查看完整真实记录；公开页提供结果、配置及原始文件哈希，原始任务全文不集中公开再分发。

## 另外6项原生组件已经接入

|工作|已接入、已检查的组件|完整算法剩余部分|
|---|---|---|
|SEABench|冻结monitor预测的原生混淆矩阵、recall/FPR|推理图、monitor训练、自进化环境|
|AgentBreeder|原生bootstrap评分统计|scaffold生成、选择和安全评估循环|
|SafeCoEvo|验证记忆与权限经验的准入、检索|Guard和在线S-Harness；GuardVPO未公开|
|SafeEvolve|原生SkillBank编译和任务检索|SkillBank更新及policy SFT/GRPO；API没有可训练权重|
|Trusting Trust|原生静态eval分类；Docker内原生YAML行为witness入口|147行为witness已实跑；原始代码自修改代理及去污染循环尚未接完|
|AgentPoison|原生ReAct攻击评分及显式分母|梯度trigger优化、embedding资源、完整检索代理|

这6项检查使用人工合同样例，**不是论文实验轨迹**。147独立环境35项离线检查全部通过；无外部源码模式28项通过、7项跳过。旧版英博云40项检查作为历史记录保留。

## Docker与剩余主实验

147原有Docker 28.5.1已验证：1 CPU、512 MiB内存、64 PID、UID 65534、无capabilities、no-new-privileges、seccomp、只读rootfs与挂载、禁网。镜像从系统Python标准库与固定PyYAML wheel离线构建并记录SHA；没有安装系统软件。仅按本次CID和匹配的项目/调用标签清理自己创建的容器。结束后无遗留容器。新增结果和重跑步骤见[完整算法与集成说明](yaml-twins.md)。

34个可信评估格按可信属性、RSI类型、S1–S4、证据层级和对照协议展开，标明每格目前是实跑、组件检查、未执行还是待集成。**T1仍为0/286个正式分数格**；858个seed单元与288个源任务演化job是设计矩阵，不能用canary补表。完整T1–T6、每格真实轨迹、任意算法组合互换和成熟版工具库仍未完成。

下一步应完善完整算法循环、冻结各基准split/数据哈希与正式recipe；147容器环境已验收，可推进原先依赖Docker的实验。API可用并不解决模型权重、原生环境、GuardVPO未公开或推理轨迹不可访问的问题。
