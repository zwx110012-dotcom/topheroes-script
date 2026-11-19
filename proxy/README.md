# 游戏专用代理源码

这里保存已验证的启动包装器、TCP 回环转发器、模块预检与恢复脚本、C# 补丁工具及离线测试。它与截图采集助手是两个独立组件。仅上传自有源码，不包含游戏原始模块、修改后的模块、账号数据、运行日志或本机配置。

## 构建与离线测试

需要 Windows、PowerShell 7、Python 3.11+、.NET Framework 4 C# 编译器，以及 [Mono.Cecil](https://github.com/jbevain/cecil) 0.11.x。取得 Mono.Cecil.dll 后运行：

```powershell
.\build_proxy.ps1 -CecilPath 'C:\Dependencies\Mono.Cecil.dll'
pwsh -NoProfile -File .\tests\test_recovery.ps1
python .\tests\test_tcp_relay.py
```

构建只生成本目录的工具；测试只使用临时模拟模块及回环连接，不启动游戏、不更改 Steam 配置或系统网络。

## 本机配置与版本限制

- `TOPHEROES_GAME_ROOT`：包含 `TopHeroes_Steam.exe` 的游戏安装目录。
- `TOPHEROES_PYTHON`：Python 可执行文件；未设置时从 PATH 查找。
- `TOPHEROES_PWSH`：PowerShell 7 可执行文件；默认使用 Program Files 下的 PowerShell 7。
- 热更新缓存根据当前用户目录计算；实际模块路径同时受安装目录与缓存目录白名单约束。
- `manifest.example.json` 是版本哈希格式示例，不能直接当作安装包。使用启动器前必须准备与自己当前游戏版本完全匹配的原件、补丁及 `manifest.json`，并填写实际目标路径。

补丁工具含历史版本的哈希和方法结构检查；游戏更新后必须重新审查、适配及验证。缺少匹配模块时应拒绝启动，不能通过放宽哈希检查或复制旧模块来绕过。该源码归档不提供适用于任意游戏版本的一键代理安装。

## 审查边界

助手从截图识别并点击；本目录代理会临时修改经校验的游戏模块连接入口。退出后只还原仍与补丁哈希一致的文件，保留游戏更新产生的新文件或已删除的缓存模块。不能将整个仓库描述为“完全不修改游戏文件”。

转发器仅监听回环地址，转发 TCP 字节，不解密 TLS；不修改系统代理、VPN、DNS、路由、证书、服务或驱动。不保证覆盖游戏使用的所有网络组件。

历史验证：2026-11-04 在当时的游戏 1.127.11 上确认资源下载、进入游戏和服务器时间同步。该结果不代表未来更新兼容，也不代表本次上传重新进行了实机测试。
