"""
AstrBot 聊天限制器插件

功能描述：
- 管理员可将用户添加至受信任名单
- 受信任用户：群聊和私聊均可使用AI
- 非受信任用户：仅限群聊使用AI，私聊将被拒绝
- 支持按平台启用限制，未启用的平台直接放行
- 白名单按平台区分，同一ID在不同平台可独立设置
- 只在不信任用户发送消息并触发AI回复时拦截，只回复一次警告

作者: HamLJYH
版本: 1.0.3
日期: 2026-09-10
"""

import os
import json
from typing import Dict, List, Set
from pathlib import Path

from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class ChatLimiterPlugin(Star):
    """聊天限制器插件主类"""

    # 默认配置
    DEFAULT_ENABLE_LIMITER = True
    DEFAULT_DENY_MESSAGE = "您不在受信任名单中，无法通过私聊使用AI。请联系管理员添加信任。"
    DEFAULT_ENABLED_PLATFORMS: List[str] = []

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_chat_limiter"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.trust_list_file = self.data_dir / "trust_list.json"
        self.trust_list: Dict[str, Set[str]] = {}

        self._load_trust_list()
        self.enabled_platforms: List[str] = self._get_enabled_platforms()

        total_users = sum(len(users) for users in self.trust_list.values())
        logger.info(
            f"[ChatLimiter] 插件已加载，"
            f"受信任用户总数: {total_users}, "
            f"启用限制的平台: {self.enabled_platforms if self.enabled_platforms else '所有平台'}"
        )

    def _load_trust_list(self) -> None:
        """从文件加载受信任用户列表"""
        if self.trust_list_file.exists():
            try:
                with open(self.trust_list_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if "trusted_users" in data and isinstance(data["trusted_users"], list):
                    old_ids = data["trusted_users"]
                    self.trust_list = {"default": set(old_ids)}
                    logger.info(f"[ChatLimiter] 检测到旧格式白名单，已迁移 {len(old_ids)} 个ID到 default 平台")
                    self._save_trust_list()
                elif "trusted_users" in data and isinstance(data["trusted_users"], dict):
                    self.trust_list = {
                        platform: set(users)
                        for platform, users in data["trusted_users"].items()
                    }
                else:
                    self.trust_list = {}

                total = sum(len(users) for users in self.trust_list.values())
                logger.info(f"[ChatLimiter] 已加载白名单，共 {total} 个受信任用户")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"[ChatLimiter] 加载白名单失败: {e}")
                self.trust_list = {}
        else:
            self._save_trust_list()

    def _save_trust_list(self) -> None:
        """保存受信任用户列表到文件"""
        try:
            with open(self.trust_list_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "trusted_users": {
                            platform: sorted(list(users))
                            for platform, users in sorted(self.trust_list.items())
                        }
                    },
                    f,
                    ensure_ascii=False,
                    indent=2
                )
        except IOError as e:
            logger.error(f"[ChatLimiter] 保存白名单失败: {e}")

    def _get_enabled_platforms(self) -> List[str]:
        """获取启用限制的平台列表"""
        platforms = self.config.get("enabled_platforms", self.DEFAULT_ENABLED_PLATFORMS)
        if not platforms:
            return []
        return [str(p).strip().lower() for p in platforms if str(p).strip()]

    def _get_event_platform(self, event: AstrMessageEvent) -> str:
        """获取消息来源平台名称"""
        try:
            platform_name = event.get_platform_name()
            if platform_name:
                return str(platform_name).strip().lower()
        except Exception as e:
            logger.warning(f"[ChatLimiter] 获取平台信息失败: {e}")
        return "default"

    def _is_platform_enabled(self, event: AstrMessageEvent) -> bool:
        """检查消息来源平台是否启用了限制"""
        if not self.enabled_platforms:
            return True

        platform_name = self._get_event_platform(event)

        if platform_name in self.enabled_platforms:
            logger.debug(f"[ChatLimiter] 平台 {platform_name} 已启用限制，进入信任判断")
            return True
        else:
            logger.debug(f"[ChatLimiter] 平台 {platform_name} 未启用限制，直接放行")
            return False

    def _is_trusted(self, event: AstrMessageEvent) -> bool:
        """检查用户是否在受信任名单中"""
        sender_id = str(event.get_sender_id())
        platform = self._get_event_platform(event)

        if sender_id in self.trust_list.get(platform, set()):
            return True

        if platform != "default" and sender_id in self.trust_list.get("default", set()):
            return True

        return False

    def _get_deny_message(self) -> str:
        """获取拒绝提示消息"""
        return self.config.get("deny_private_message", self.DEFAULT_DENY_MESSAGE)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """LLM 请求拦截器 - 在AI准备回复前进行权限检查"""
        if not self.DEFAULT_ENABLE_LIMITER:
            return

        if not self._is_platform_enabled(event):
            return

        if not event.is_private_chat():
            return

        if event.role == "admin":
            return

        if self._is_trusted(event):
            return

        deny_msg = self._get_deny_message()
        await event.send(event.plain_result(deny_msg))
        event.stop_event()

    # ==================== 指令 ====================

    @filter.command("trust")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_trust(self, event: AstrMessageEvent, user_id: str = None, platform: str = None):
        """添加用户到受信任名单

        用法: /trust <ID> [平台]
        示例: /trust 123456789           -> 添加到全局白名单（所有平台生效）
              /trust 123456789 aiocqhttp  -> 仅添加到 aiocqhttp 平台白名单
        """
        if not user_id:
            yield event.plain_result(
                "请提供用户ID\n"
                "用法: /trust <ID> [平台]\n"
                "示例: /trust 123456789\n"
                "       /trust 123456789 aiocqhttp"
            )
            return

        user_id = str(user_id).strip()

        if not user_id.isdigit() or not (5 <= len(user_id) <= 20):
            yield event.plain_result(f"无效的ID: {user_id}\nID必须是5-20位纯数字")
            return

        if platform:
            target_platform = str(platform).strip().lower()
        else:
            target_platform = "default"

        if target_platform not in self.trust_list:
            self.trust_list[target_platform] = set()

        if user_id in self.trust_list[target_platform]:
            platform_label = "全局" if target_platform == "default" else target_platform
            yield event.plain_result(f"用户 {user_id} 已在 {platform_label} 受信任名单中")
            return

        self.trust_list[target_platform].add(user_id)
        self._save_trust_list()

        platform_label = "全局（所有平台）" if target_platform == "default" else target_platform
        yield event.plain_result(f"已添加用户 {user_id} 到 {platform_label} 受信任名单")
        logger.info(f"[ChatLimiter] 管理员 {event.get_sender_id()} 添加用户 {user_id} 到 {target_platform} 白名单")

    @filter.command("untrust")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_untrust(self, event: AstrMessageEvent, user_id: str = None, platform: str = None):
        """从受信任名单中移除用户

        用法: /untrust <ID> [平台]
        示例: /untrust 123456789           -> 从全局白名单移除
              /untrust 123456789 aiocqhttp  -> 从 aiocqhttp 平台白名单移除
        """
        if not user_id:
            yield event.plain_result(
                "请提供用户ID\n"
                "用法: /untrust <ID> [平台]\n"
                "示例: /untrust 123456789\n"
                "       /untrust 123456789 aiocqhttp"
            )
            return

        user_id = str(user_id).strip()

        if platform:
            target_platform = str(platform).strip().lower()
            if target_platform not in self.trust_list or user_id not in self.trust_list[target_platform]:
                yield event.plain_result(f"用户 {user_id} 不在 {target_platform} 平台的受信任名单中")
                return
            self.trust_list[target_platform].discard(user_id)
            self._save_trust_list()
            yield event.plain_result(f"已从 {target_platform} 平台受信任名单移除用户 {user_id}")
            logger.info(f"[ChatLimiter] 管理员 {event.get_sender_id()} 从 {target_platform} 白名单移除用户 {user_id}")
        else:
            removed_platforms = []
            for plat, users in self.trust_list.items():
                if user_id in users:
                    users.discard(user_id)
                    removed_platforms.append(plat)
            self._save_trust_list()

            if removed_platforms:
                labels = ["全局" if p == "default" else p for p in removed_platforms]
                yield event.plain_result(f"已从以下平台移除用户 {user_id}: {', '.join(labels)}")
                logger.info(f"[ChatLimiter] 管理员 {event.get_sender_id()} 从白名单移除用户 {user_id}，涉及平台: {removed_platforms}")
            else:
                yield event.plain_result(f"用户 {user_id} 不在任何受信任名单中")

    @filter.command("trustlist")
    async def cmd_trustlist(self, event: AstrMessageEvent, platform: str = None):
        """查看受信任用户名单

        用法: /trustlist [平台]
        示例: /trustlist            -> 查看所有平台的白名单
              /trustlist aiocqhttp  -> 仅查看 aiocqhttp 平台的白名单
        """
        if platform:
            target_platform = str(platform).strip().lower()
            users = self.trust_list.get(target_platform, set())

            if not users:
                yield event.plain_result(f"平台 {target_platform} 的受信任名单为空")
                return

            lines = [f"平台 {target_platform} 受信任名单", "=" * 20]
            for i, uid in enumerate(sorted(users), 1):
                lines.append(f"{i}. {uid}")
            lines.append(f"\n共 {len(users)} 人")
            yield event.plain_result("\n".join(lines))
        else:
            if not self.trust_list:
                yield event.plain_result("受信任名单为空\n使用 /trust <ID> [平台] 添加用户")
                return

            lines = ["受信任用户名单（按平台）", "=" * 20]
            total = 0
            for plat in sorted(self.trust_list.keys()):
                users = self.trust_list[plat]
                if not users:
                    continue
                label = "全局" if plat == "default" else plat
                lines.append(f"\n[{label}] ({len(users)}人)")
                for i, uid in enumerate(sorted(users), 1):
                    lines.append(f"  {i}. {uid}")
                total += len(users)
            lines.append(f"\n总计: {total} 人")
            yield event.plain_result("\n".join(lines))

    @filter.command("checktrust")
    async def cmd_checktrust(self, event: AstrMessageEvent, user_id: str = None):
        """检查指定用户是否在受信任名单中

        用法: /checktrust <ID>
        """
        if not user_id:
            yield event.plain_result("请提供用户ID\n用法: /checktrust <ID>")
            return

        target_id = str(user_id).strip()

        found_platforms = []
        for plat, users in self.trust_list.items():
            if target_id in users:
                found_platforms.append(plat)

        if found_platforms:
            labels = ["全局" if p == "default" else p for p in sorted(found_platforms)]
            yield event.plain_result(f"用户 {target_id}\n受信任用户\n生效平台: {', '.join(labels)}")
        else:
            yield event.plain_result(f"用户 {target_id}\n不在受信任名单中\n私聊功能已被限制")

    @filter.command("limiterplatforms")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_limiter_platforms(self, event: AstrMessageEvent):
        """查看当前启用限制的平台列表"""
        if not self.enabled_platforms:
            yield event.plain_result(
                "当前所有平台均启用聊天限制\n"
                "如需限制特定平台，请在配置中设置 enabled_platforms"
            )
            return

        lines = ["启用聊天限制的平台列表", "=" * 20]
        for i, platform in enumerate(self.enabled_platforms, 1):
            lines.append(f"{i}. {platform}")
        lines.append(f"\n共 {len(self.enabled_platforms)} 个平台")

        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        """插件卸载时保存数据"""
        self._save_trust_list()
        logger.info("[ChatLimiter] 插件已安全卸载")