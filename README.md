# 诗矩

*不必等到精通格律，才开始创作自己的诗词。*

## 背景

### 通用大模型难稳定遵守格律

让 AI 写一首七律、填一阕词，很快就能得到一篇文字。但字数是否正确、平仄是否合适、韵脚是否一致，仍不能只凭它读起来像诗来判断。

这也是研究中观察到的问题。Qu 等人发表于 **AAAI 2026** 的 **PoeTone**[1]，对 18 个大语言模型在不同提示策略下的宋词生成表现进行了系统评测，发现受测模型在遵循结构、平仄和押韵规则方面仍有不足。

### 字数控制、格式与押韵

Yu 等人发表于 **ACL 2024 系统演示论文集**的 **CharPoet**[2]，通过调整分词与输出方式，并继续训练，改善诗词的字数控制。

Li 等人发表于 **ACL 2020 主会论文集**的《**Rigid Formats Controlled Text Generation**》提出了 **SongNet**[3]，通过专门的格式符号、注意力机制设计及训练，改善格式与押韵表现。

### 人机协作，让用户参与推敲

Ma 等人发表于 **EACL 2023 系统演示论文集**的 **Yu Sheng**[4]，将诗歌生成与局部润色结合起来，让用户参与作品的打磨。这种人机协作方式，也是我们所期待的创作体验。

### 无需额外训练，在生成过程中引入约束

Zou 发表于 **ACL 2025 长文论文集**的 **BIPro**[5]，探索了无需额外训练、利用块生成模型进行唐诗生成与修订的方法。这启发我们尝试自回归模型的格律诗词生成。

## 诗矩：无需额外训练，按指定诗体、词牌和韵书生成

诗矩将字数、句读、平仄和押韵规则用于生成过程，**希望更多的通用自回归模型无需额外训练，就能按指定诗体、宋词词牌和韵书进行创作。**

你可以选择想写的形式和主题，让本地模型起稿；如果佳句“妙手偶得之”，也可以让AI补完其余部分，创作出完整作品；对某些句子的表达不满意，还可以指定改写，继续推敲。

## 不懂格律，也能边创作、边学习

你可以先和 Agent 聊想写什么，一起斟酌情绪、意象和用韵。它会调用工具查词谱、查韵书，把讨论整理成可编辑的创作方案；你检查并提交后，由本地模型按格律生成正文。

成稿后，还可以请 Agent 核对格律、讨论修改方向，再让本地模型改写或补完。你决定留下什么、调整什么，在创作中逐渐了解诗词的规矩。

**不配置 API，也能使用。** 准备好本地模型后，你可以自行填写要求，进行生成、改写和补完；格律检测无需配置模型。

**接了 API，为什么还要运行本地模型？** 诗矩需要在模型每一步选字时，直接排除不符合格律的候选。普通对话 API 不开放这一层控制，因此仍需在本地运行模型。

## 从这里开始

安装和启动都有图形界面，无需提前安装 Python 或 Conda，也不用输入命令。

### 第一次使用：跟着安装向导走

1. [下载源码 ZIP](https://github.com/Plant-Paramour/ShiJv/archive/refs/heads/main.zip)，右键选择“全部解压”。请保留完整目录，不要在压缩包里运行。
2. 双击 **`诗矩环境部署.bat`**，点击“检测电脑”，查看推荐的模型和运行方案。不确定怎么选时，可以先看[推荐配置](推荐配置.md)。
3. 确认模型和保存位置，点击“检查安装方案”，查看下载量和磁盘空间，再点击“开始安装”。工具会自动准备环境、下载或核验模型，并测试实际生成。
4. 验证通过后，点击 **“打开诗矩”**。

首次安装需要下载模型和运行组件，请耐心等待。中途失败可重试，重新打开工具后也可以“恢复上次部署”，已下载的完整文件会复用。详细操作见[环境部署说明](docs/environment-tool-user.md)。

### 以后使用：打开启动器

双击 **`诗矩单机版.bat`**，选择已记住的环境与模型配对，点击“直接启动”。浏览器会打开诗矩；若需手动访问，请使用启动器显示的地址。

### 想让 Agent 一起参与创作

打开“模型与运行设置”，填写你使用的 API 服务地址、模型名称和密钥，即可与 Agent 讨论创作、查阅资料和准备方案。**这一步可跳过，不影响本地模型生成和格律检测。**

## 接下来可以看

- [使用说明](使用说明.md)：启动、配置模型、改写补完，以及备份和常见问题。
- [推荐配置](推荐配置.md)：按电脑配置选模型，了解资源占用和等待速度。
- [环境部署说明](docs/environment-tool-user.md)：自动安装、恢复下载及高级选项。
- [启动器详细说明](docs/single-player-launcher.html)：启动器的具体操作。

## 数据保存在哪里

诗矩在本机浏览器中使用，无需注册或登录，不会自动向开发者上传你的作品和使用数据。作品和对话默认保存在本机的 `%LOCALAPPDATA%\ShiJu`。数据、备份和模型配置没有应用层加密，请妥善保存，尤其不要把含密钥的配置上传到公共仓库。

使用远程对话服务时，对话、附件及 API 密钥会发送到你填写的服务地址；下载模型和运行组件也需要联网。

## 开发与源码导出

开发依赖安装命令：`python -m pip install -e ".[api,dev]"`。

主要代码在 `apps/`（本地 API、Agent、启动器和 Worker）、`shiju/`（格律与生成引擎）和 `web/prosody-checker/`（前端）；韵书和词谱在 `Rhyme/`、`Songci_Meter/`。

导出源码：`python tools/build_single_player_source.py --output dist/single-player-source.zip`，输出路径须尚不存在。导出包不含本地测试、安装材料、用户数据、模型权重、运行日志、本地归档或 Git 历史。发布源码时请使用导出包，避免直接上传开发工作区。

本地测试不随仓库和源码包发布。模型能否加载、CUDA 是否可用及实际生成效果，仍需在目标电脑上检查。

## 参考文献

[1] Qu, Z., Yuan, S., & Färber, M. (2026). [PoeTone: A Framework for Constrained Generation of Structured Chinese Songci with LLMs](https://ojs.aaai.org/index.php/AAAI/article/view/40555). *Proceedings of the AAAI Conference on Artificial Intelligence, 40*(39), 32764–32772. DOI: [10.1609/aaai.v40i39.40555](https://doi.org/10.1609/aaai.v40i39.40555).

[2] Yu, C., Zang, L., Wang, J., Zhuang, C., & Gu, J. (2024). [CharPoet: A Chinese Classical Poetry Generation System Based on Token-free LLM](https://aclanthology.org/2024.acl-demos.30/). In *Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics (Volume 3: System Demonstrations)* (pp. 315–325). Association for Computational Linguistics. DOI: [10.18653/v1/2024.acl-demos.30](https://doi.org/10.18653/v1/2024.acl-demos.30).

[3] Li, P., Zhang, H., Liu, X., & Shi, S. (2020). [Rigid Formats Controlled Text Generation](https://aclanthology.org/2020.acl-main.68/). In *Proceedings of the 58th Annual Meeting of the Association for Computational Linguistics* (pp. 742–751). Association for Computational Linguistics. DOI: [10.18653/v1/2020.acl-main.68](https://doi.org/10.18653/v1/2020.acl-main.68).

[4] Ma, J., Zhan, R., & Wong, D. F. (2023). [Yu Sheng: Human-in-Loop Classical Chinese Poetry Generation System](https://aclanthology.org/2023.eacl-demo.8/). In *Proceedings of the 17th Conference of the European Chapter of the Association for Computational Linguistics: System Demonstrations* (pp. 57–66). Association for Computational Linguistics. DOI: [10.18653/v1/2023.eacl-demo.8](https://doi.org/10.18653/v1/2023.eacl-demo.8).

[5] Zou, X. (2025). [BIPro: Zero-shot Chinese Poem Generation via Block Inverse Prompting Constrained Generation Framework](https://aclanthology.org/2025.acl-long.56/). In *Proceedings of the 63rd Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)* (pp. 1116–1134). Association for Computational Linguistics. DOI: [10.18653/v1/2025.acl-long.56](https://doi.org/10.18653/v1/2025.acl-long.56).
