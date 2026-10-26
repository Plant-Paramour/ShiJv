# 诗矩单机版前端

前端由本地 API 同源托管，提供 AI 对话、格律检测、作品管理和运行设置。请从仓库根目录的启动器打开应用，安装步骤见 [项目 README](../../README.md)。

韵书和词谱来自 `Rhyme/`、`Songci_Meter/`。前端使用原生 JavaScript 模块，运行应用不需要 npm 构建。

前端回归测试：

```powershell
node --test web/prosody-checker/tests/*.test.mjs
```
