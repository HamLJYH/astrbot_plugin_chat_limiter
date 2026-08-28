"""
AstrBot 聊天限制器插件

功能描述：
- 管理员可将用户添加至受信任名单
- 受信任用户：群聊和私聊均可使用AI
- 非受信任用户：仅限群聊使用AI，私聊将被拒绝
- 只在不信任用户发送消息并触发AI回复时拦截，只回复一次警告

作者: HamLJYH
版本: 1.0.1
日期: 2026-08-28
"""

import os
import json
from typing import Set
from pathlib import Path

from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class ChatLimiterPlugin(Star):
    """聊天限制器插件主类"""

    # 默认配置（不再从 _conf_schema.json 读取）
    DEFAULT_ENABLE_LIMITER = True  # 默认启用聊天限制
    DEFAULT_DENY_MESSAGE = "您不在受信任名单中，无法通过私聊使用AI。请联系管理员添加信任。"

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 插件数据目录：data/plugin_data/astrbot_plugin_chat_limiter/
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_chat_limiter"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 白名单数据文件
        self.trust_list_file = self.data_dir / "trust_list.json"
        self.trust_list: Set[str] = set()

        # 加载白名单
        self._load_trust_list()

        logger.info(f"[ChatLimiter] 插件已加载，当前受信任用户数量: {len(self.trust_list)}")

    def _load_trust_list(self) -> None:
        """从文件加载受信任用户列表"""
        if self.trust_list_file.exists():
            try:
                with open(self.trust_list_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.trust_list = set(data.get("trusted_users", []))
                logger.info(f"[ChatLimiter] 已加载 {len(self.trust_list)} 个受信任用户")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"[ChatLimiter] 加载白名单失败: {e}")
                self.trust_list = set()
        else:
            self._save_trust_list()

    def _save_trust_list(self) -> None:
        """保存受信任用户列表到文件"""
        try:
            with open(self.trust_list_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"trusted_users": sorted(list(self.trust_list))},
                    f,
                    ensure_ascii=False,
                    indent=2
                )
        except IOError as e:
            logger.error(f"[ChatLimiter] 保存白名单失败: {e}")

    def _is_trusted(self, event: AstrMessageEvent) -> bool:
        """检查用户是否在受信任名单中"""
        sender_id = str(event.get_sender_id())
        return sender_id in self.trust_list

    def _get_deny_message(self) -> str:
        """获取拒绝提示消息（优先读取配置，否则使用默认值）"""
        return self.config.get("deny_private_message", self.DEFAULT_DENY_MESSAGE)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求拦截器 - 在AI准备回复前进行权限检查

        注意：此钩子只在真正需要调用LLM时触发，用户刚打开聊天框不会触发。
        这里不能使用 yield 发送消息，必须使用 event.send()。
        """
        # 如果插件未启用，直接放行
        if not self.DEFAULT_ENABLE_LIMITER:
            return

        # 只处理私聊消息（群聊直接放行）
        if not event.is_private_chat():
            return

        # 管理员始终放行
        if event.role == "admin":
            return

        # 受信任用户始终放行
        if self._is_trusted(event):
            return

        # 非受信任用户私聊：发送警告并阻止LLM请求
        deny_msg = self._get_deny_message()

        # 使用 event.send() 发送警告（on_llm_request 中不能用 yield）
        await event.send(event.plain_result(deny_msg))

        # 阻止LLM请求，让AI不处理这条消息
        event.stop_event()

    @filter.command("trust")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_trust(self, event: AstrMessageEvent, user_id: str = None):
        """添加用户到受信任名单

        用法: /trust <QQ号>
        示例: /trust 123456789
        """
        if not user_id:
            yield event.plain_result("请提供用户QQ号\n用法: /trust <QQ号>")
            return

        # 清理输入
        user_id = str(user_id).strip()

        # 验证QQ号格式（纯数字，5-12位）
        if not user_id.isdigit() or not (5 <= len(user_id) <= 12):
            yield event.plain_result(f"无效的QQ号: {user_id}")
            return

        # 检查是否已在名单中
        if user_id in self.trust_list:
            yield event.plain_result(f"用户 {user_id} 已经在受信任名单中")
            return

        # 添加到白名单
        self.trust_list.add(user_id)
        self._save_trust_list()

        yield event.plain_result(f"已添加用户 {user_id} 到受信任名单")
        logger.info(f"[ChatLimiter] 管理员 {event.get_sender_id()} 添加用户 {user_id} 到白名单")

    @filter.command("untrust")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_untrust(self, event: AstrMessageEvent, user_id: str = None):
        """从受信任名单中移除用户

        用法: /untrust <QQ号>
        示例: /untrust 123456789
        """
        if not user_id:
            yield event.plain_result("请提供用户QQ号\n用法: /untrust <QQ号>")
            return

        user_id = str(user_id).strip()

        if user_id not in self.trust_list:
            yield event.plain_result(f"用户 {user_id} 不在受信任名单中")
            return

        self.trust_list.discard(user_id)
        self._save_trust_list()

        yield event.plain_result(f"已移除用户 {user_id} 的受信任权限")
        logger.info(f"[ChatLimiter] 管理员 {event.get_sender_id()} 移除用户 {user_id} 从白名单")

    @filter.command("trustlist")
    async def cmd_trustlist(self, event: AstrMessageEvent):
        """查看受信任用户名单"""
        if not self.trust_list:
            yield event.plain_result("受信任名单为空\n使用 /trust <QQ号> 添加用户")
            return

        # 构建列表文本
        lines = ["受信任用户名单", "=" * 20]
        for i, uid in enumerate(sorted(self.trust_list), 1):
            lines.append(f"{i}. {uid}")
        lines.append(f"\n共 {len(self.trust_list)} 人")

        yield event.plain_result("\n".join(lines))

    @filter.command("checktrust")
    async def cmd_checktrust(self, event: AstrMessageEvent, user_id: str = None):
        """检查指定用户是否在受信任名单中

        用法: /checktrust <QQ号>
        """
        if not user_id:
            yield event.plain_result("请提供用户QQ号\n用法: /checktrust <QQ号>")
            return

        target_id = str(user_id).strip()

        is_trusted = target_id in self.trust_list

        if is_trusted:
            yield event.plain_result(f"用户 {target_id}\n受信任用户")
        else:
            yield event.plain_result(f"用户 {target_id}\n不在受信任名单中\n私聊功能已被限制")

    async def terminate(self):
        """插件卸载时保存数据"""
        self._save_trust_list()
        logger.info("[ChatLimiter] 插件已安全卸载")
