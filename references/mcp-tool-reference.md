# MCP v1.6.0 工具契约

配套 Skill v3.7.0。完整参数快照见 [mcp-tools.json](mcp-tools.json)，实际执行时优先读取已安装 MCP 的工具定义。不要将快照的新参数发送给旧版本。

## 本轮新增与兼容行为

| 工具 | 新能力/修复 | 兼容说明 |
|---|---|---|
| network_capture | max_body_size、stop 的 wait_timeout_ms；pending/dropped 指标 | 旧 start/stop/clear/status 调用继续可用；ID 清理后不复用 |
| list_network_requests | limit/after_id 增量分页、state/body_state | 默认仍返回列表；domain 匹配修正为主机边界 |
| get_network_request | 采集和读取两层截断元数据 | 保留旧字段；truncated 不再把残缺内容标为完整 |
| export_network_capture | 有版本号的 JSON 文件导出、默认掩码 | 新工具；不重放请求，不覆盖文件 |
| cookies | name/domain 取交集、精确删除选中的 Cookie | 修复旧版扩大删除范围；无条件调用仍清空全部 |
| verify_signer_offline | 输入校验、可选 runtime=node、执行期限 | 默认仍用当前浏览器；签名函数参数为 sample.input 本身 |

`name_filter` 在 Cookie 归因工具中是子串，多个名称分别查询。`get_request_initiator` 仍从 Hook 日志启发式关联调用栈，不能因网络响应精确关联就宣称调用栈也已精确关联。

## 主世界与 Frame

`evaluate_js` 默认 isolated，页面自有对象用 main。`get_page_info().frames` 提供快照，持久 Hook 用 frame_url/frame_name，不能用 frame_index。`pending` 不是捕获成功；用相同 world/Frame 的 get_trace_data 验证。

清理 Trace 不等于卸载 Hook；requires_relaunch 表示锁定属性或初始化脚本需要重建 Context。Attach 模式仅重连无法清掉外部浏览器的初始化脚本。主世界表达式执行失败后不自动换通道重放。

## 已移除的历史能力

Session/Assertion 工具在 MCP v1.0.0 起已移除，使用需求工作区的 manifest/fixture 与 cases 文件。旧文档中的 start_network_capture、trace_function、set_breakpoint_via_hook 等不能直接调用；分别使用 network_capture(action='start')、hook_function(mode='trace')、get_trace_data。历史 API 列表可通过 Git 旧版本查阅，不作为现行调用依据。

## 更新契约快照

维护者在两个仓库同级时运行：

```bash
python scripts/check-mcp-contract.py --mcp-root ../camoufox-reverse-mcp
python scripts/check-mcp-contract.py --mcp-root ../camoufox-reverse-mcp --write
```

第一次比较快照，工具或参数发生变化时失败；第二次在完成兼容检查后更新快照。该脚本需要 MCP 仓库的 Python 依赖，仅读取工具注册，不启动浏览器。
