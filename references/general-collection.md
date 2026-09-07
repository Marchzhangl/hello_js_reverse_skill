# 通用采集、恢复与改版基线

适用于 JSON API。接口鉴权、动态签名与业务字段由具体需求配置或回调提供；MCP 负责捕获和验证证据，不执行站点特例。

## 配置运行

复制 `templates/python-request/` 到需求工作区，安装其 requirements，再将 `config/collection.example.json` 复制为私有任务配置并替换 URL/字段。

```bash
python collect.py --config config/job.json --output artifacts/items.jsonl
python collect.py --config config/job.json --output artifacts/items.jsonl --resume
```

- `items_path` 是 JSON 点路径，例如 `data.items`；空字符串表示顶层数组，数组索引可以写成 `data.0.items`。包含点号的真实属性名请通过自定义 `extract_items` 回调读取。
- `success_path/success_value` 校验业务成功状态；HTTP 成功不代表采集成功。无业务状态字段时可省略，仍需验证数据结构。
- `item_key` 可选，例如 `id`；设置后跨页和恢复过程按该字段去重。不配置时保留原始条目。
- `pagination.mode` 为 `page/offset/cursor/none`。`param` 指定字段；`in="body"` 将分页字段放进 JSON Body，默认在 query。页码默认从 1 开始；offset 默认从 0 开始，并显式设置 `step` 为接口要求的偏移增量。Cursor 指定 `next_path`，下一游标为 null/空字符串时结束，数字 0 仍是有效游标。
- `max_pages` 是包含已完成页数的总上限，默认 100；达到上限返回 `limited`，不会无限翻页。提高上限后可恢复。
- `headers/cookies/params/body/method/timeout` 都来自任务配置。登录态缺失或业务校验失败时停止，不把错误响应当空页。
- GET/HEAD/OPTIONS 对连接异常、超时、429/5xx 有界重试；POST 等不默认重放。自定义客户端可显式设置 `retry_non_idempotent=True`，调用者应先确认业务幂等条件。

## 自定义签名与分页适配

```python
from utils.collector import collect_to_jsonl
from utils.request import RequestClient

client = RequestClient()

def fetch_page(cursor):
    params = {"page": cursor}
    # 在这里用已验证的签名函数处理 params/body/headers；每次请求现算。
    response = client.get("https://example.test/api/items", params=params)
    payload = response.json()
    if payload["code"] != 0:
        raise ValueError("API business status failed")
    return payload

try:
    result = collect_to_jsonl(
        fetch_page,
        extract_items=lambda payload: payload["data"]["items"],
        next_cursor=lambda payload, current: current + 1 if payload["data"]["has_more"] else None,
        output_path="artifacts/items.jsonl",
        job_key="items-api-v1-signer-v2",  # 语义变化时更新，不填写凭据
        item_key=lambda item: item["id"],
        max_pages=100,
    )
finally:
    client.close()
```

签名函数接收 URL/Body/时间/随机输入的方式由证据确定。不要在通用组件里加入目标域名或固定 Cookie。需要 jsdom/第三方 JS SDK 时在需求项目建立独立签名模块，并用 fixture 验证。

## 恢复语义

每页完整验证并序列化后写 JSONL，flush/fsync 后原子更新 checkpoint。恢复时核对 job key、输出路径、已提交前缀的 SHA-256；未提交的尾部写入会被截除，然后从 checkpoint 的下一游标继续。

- 这是本地输出的页级恢复，不代表远端请求恰好执行一次。崩溃后可能重新请求最后未提交的一页；该回调应为读取操作或具有幂等保证。
- 查询/接口/签名逻辑变化需使用新 job key 或新输出。CLI 配置哈希排除可刷新 Headers/Cookie，但这些字段若代表不同账号/数据范围，应换输出，不能混合数据。
- 为防止两个实例写同一文件，运行持有 `.lock`。强制终止后可能留下 stale lock，确认旧进程已结束再移除锁；不要自动抢锁。
- 去重键和已访问游标存放在 checkpoint，适用于有界任务。大规模长任务应改用数据库索引/任务队列，不能把内存集合称为无限容量。
- 若要求追踪数据更新而不是按主键保留第一次出现，请省略 `item_key` 或按版本构造复合键。

## 捕获样本与独立验签

先在 MCP 中 `network_capture(start)`，触发操作，再 `network_capture(stop, wait_timeout_ms=3000)`。检查 pending、dropped、body_state 和截断元数据后调用 `export_network_capture`。默认导出掩码版，需要原始 Headers/Query/Body 时显式开启并保留在私有目录。

`verify_signer_offline(..., runtime="node")` 不启动浏览器；默认 browser runtime 保留旧行为。独立进程支持异步函数与 `crypto/node:crypto`，有总运行期限；复杂签名工程使用自己的 Node 项目测试入口。两种方式都要求非空 expected，字段缺失不能算通过。

## 改版基线

使用 Skill 的绝对脚本路径，在需求项目根目录执行：

```bash
python /path/to/skill/scripts/project-baseline.py create --root . \
  --files config/sdk.js utils/sign.py fixtures/samples.json \
  --mcp-version 1.6.0 --browser-version 152.0.4-beta.30
python /path/to/skill/scripts/project-baseline.py check --root .
```

脚本只记录相对路径、SHA-256 与版本字符串，不写源码或凭据；不会覆盖已有 manifest。`check` 返回 changed/missing，检测到差异退出码为 1。需要新基线时使用新的 `--manifest` 路径，保留旧版本供比较。版本号由实际运行环境填写，哈希未变仍需行为回归。

## 依赖与离线测试

```bash
python /path/to/skill/scripts/check-deps.py --mode python --json
python /path/to/skill/scripts/check-deps.py --mode browser --json
python test.py
```

`check-deps.sh` 仍可用，通过 `JS_REVERSE_PYTHON` 选择解释器。Node 依赖在复制后的模板目录安装，不假设全局 npm 包可以被项目加载。模板的 `npm test` 和 `node main.js --test` 均为离线测试；它们验证通用组件，不表示目标站点已经采集成功。
