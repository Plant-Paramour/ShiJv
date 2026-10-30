# Windows 安装程序试制说明

本试制为现有诗矩环境部署工具增加 Inno Setup 外壳。安装器只安装经源码发布检查的程序文件，创建开始菜单／可选桌面入口，并登记当前用户级卸载项。首次打开仍由原来的 `tools/environment-tool.ps1` 执行 CPU／GPU 选择、依赖下载、模型校验和真实生成验收。

旧的 `诗矩环境部署.bat`、`诗矩单机版.bat` 与 PowerShell 脚本继续保留，作为新安装入口不可用时的备用方式。安装器不会删除或迁移旧目录、模型、缓存或用户数据。

## 生成安装包

使用指定的 Python 环境准备已审核载荷：

```powershell
& 'C:\ProgramData\anaconda3\envs\ShiJu_new\python.exe' tools/build_windows_installer.py --stage-only --payload dist/windows-installer-payload-v0.1.0
```

安装 Inno Setup 6 或 7 后，编译正式试制 EXE：

```powershell
& 'C:\ProgramData\anaconda3\envs\ShiJu_new\python.exe' tools/build_windows_installer.py --payload dist/windows-installer-payload-v0.1.0 --output dist/windows-installer-v0.1.0 --version 0.1.0
```

如果编译器未自动找到，可加 `--iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'`。所有输出都必须使用 `dist` 下新的目录；脚本不会覆盖已有载荷或安装程序目录。正式对外发布前还需单独完成 Windows 代码签名、干净 Windows 安装／卸载验证和 GPU／CPU 部署验收。

## 安装与回退行为

- 默认安装到当前用户的 `%LOCALAPPDATA%\Programs\ShiJu`，不请求管理员权限。
- 安装完成后可启动“诗矩安装向导”；它就是现有环境部署器，不是第二套部署实现。
- 用户仍可以从安装目录运行 `诗矩环境部署.bat` 或 `诗矩单机版.bat` 作为旧入口。
- 卸载只移除安装目录中由安装器记录的程序文件和快捷方式；模型、运行环境、部署记录和用户数据存放在独立数据目录，卸载会保留它们。
- 本试制不提供自动升级器，不迁移用户已有的解压版，也不更改资源缓存路径。
