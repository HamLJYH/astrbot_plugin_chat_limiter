## AstrBot 聊天限制器插件

限制用户与 AI 聊天的白名单插件。支持按平台启用限制，白名单按平台独立管理。

## 功能

- 白名单管理：管理员可将用户添加至受信任名单，按平台区分
- 平台限制：可配置仅对特定平台启用限制，未启用的平台直接放行
- 权限控制：
  - 受信任用户：群聊和私聊均可使用 AI
  - 非受信任用户：仅限群聊使用 AI，私聊将被拒绝

## 安装

1. 将插件文件夹复制到 AstrBot 的 `data/plugins/` 目录下
2. 重启 AstrBot 或热重载插件
3. 在 WebUI 中配置插件参数

## 指令

指令	权限	说明	
`/trust <ID> [平台]`	管理员	添加用户到受信任名单，不指定平台则为全局	
`/untrust <ID> [平台]`	管理员	从受信任名单移除用户，不指定平台则移除所有平台	
`/trustlist [平台]`	所有人	查看受信任用户名单，不指定平台则显示全部	
`/checktrust <ID>`	所有人	检查用户信任状态及生效平台	
`/limiterplatforms`	管理员	查看当前启用限制的平台列表	

## 指令示例

```
/trust 123456789              → 添加到全局白名单（所有平台生效）
/trust 123456789 aiocqhttp    → 仅添加到 aiocqhttp 平台白名单

/untrust 123456789            → 从所有平台移除
/untrust 123456789 telegram   → 仅从 telegram 平台移除

/trustlist                    → 查看所有平台的白名单
/trustlist aiocqhttp          → 仅查看 aiocqhttp 平台
```

## 配置项

在 AstrBot WebUI → 插件配置中可调整：

配置项	类型	说明	
`deny_private_message`	string	非白名单用户私聊时的拒绝提示	
`enabled_platforms`	list	启用聊天限制的平台列表，留空表示所有平台都启用	

## 支持的平台适配器
- 已在aiocghttp (NapCat) 完成测试
- 其他平台理论上可用，但未验证

## 平台配置示例

```json
// 只限制 QQ 和 Telegram，其他平台不限制
["aiocqhttp", "telegram"]

// 留空 [] 表示所有平台都启用限制
```

## 工作原理

插件通过监听 LLM 请求事件，在 AI 处理前进行拦截：

1. 检查平台是否在启用列表中 → 未启用则直接放行
2. 检查消息发送者是否为管理员 → 放行
3. 检查消息发送者是否在白名单（当前平台 + 全局）→ 放行
4. 检查是否为私聊 → 拒绝并提示
5. 群聊消息 → 放行

## 白名单数据结构

```json
{
  "trusted_users": {
    "default": ["123456789"],
    "aiocqhttp": ["987654321"],
    "telegram": ["555666777"]
  }
}
```

- `default` 平台 = 全局白名单，所有平台均生效
- 旧版本数据会自动迁移到 `default` 平台

## 注意事项

- 管理员始终不受限制
- 白名单数据保存在 `data/plugin_data/astrbot_plugin_chat_limiter/trust_list.json`
- 非白名单用户私聊时，插件会阻止 LLM 请求，AI 不会处理该消息
- 同一 ID 可在不同平台独立设置信任状态