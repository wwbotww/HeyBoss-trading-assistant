"""只更新持久化审批状态、绝不下单的 Telegram Bot。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from trading_assistant.live.config import load_live_settings
from trading_assistant.notify.messages import (
    approval_card,
    decided_card,
    order_notification,
    workflow_alert,
)
from trading_assistant.storage.repository import TradingRepository

LOGGER = logging.getLogger(__name__)
TelegramApplication = Application[Any, Any, Any, Any, Any, Any]


class ApprovalBot:
    """轮询 SQLite 审批邮箱并与单一 Telegram chat 交互。"""

    def __init__(
        self,
        *,
        token: str,
        database_url: str,
        chat_id: str | None,
        workflow_scope: str,
        poll_interval_seconds: float,
    ) -> None:
        self._token = token
        self._repository = TradingRepository(database_url)
        self._chat_id = chat_id
        self._workflow_scope = workflow_scope
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_task: asyncio.Task[None] | None = None

    def build_application(self) -> TelegramApplication:
        """构建可运行或可注入测试的 PTB Application。"""
        application = (
            Application.builder()
            .token(self._token)
            .post_init(self._start_polling)
            .post_shutdown(self._shutdown)
            .build()
        )
        application.add_handler(CommandHandler("start", self._start_command))
        application.add_handler(
            CallbackQueryHandler(self._decision_callback, pattern=r"^(approve|deny):")
        )
        return application

    async def _start_command(
        self,
        update: Update,
        _: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        await message.reply_text(
            f"Trading Assistant 已连接。当前 chat ID: {chat.id}\n"
            "请将该值写入 TELEGRAM_CHAT_ID 后重启 approval-bot。"
        )

    async def _decision_callback(
        self,
        update: Update,
        _: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if query is None or query.data is None:
            return
        chat_id = str(query.message.chat.id) if query.message is not None else None
        if self._chat_id is None or chat_id != self._chat_id:
            await query.answer("无权操作此审批", show_alert=True)
            return
        action, event_id = query.data.split(":", maxsplit=1)
        workflow = self._repository.get_signal_workflow(event_id)
        if workflow is None or workflow.scope != self._workflow_scope:
            await query.answer("信号不存在", show_alert=True)
            return
        now_ns = time.time_ns()
        if int(workflow.expires_at_utc.timestamp() * 1_000_000_000) <= now_ns:
            expired_ids = self._repository.expire_pending(
                scope=self._workflow_scope,
                timestamp_ns=now_ns,
            )
            if event_id in expired_ids:
                self._repository.record_approval(
                    workflow.to_event(),
                    approval_mode="manual",
                    decision="EXPIRED",
                    reason="signal expired while awaiting Telegram approval",
                    timestamp_ns=now_ns,
                )
            await query.answer("信号已过期", show_alert=True)
            return
        approved = action == "approve"
        if not self._repository.decide_manual_approval(
            event_id,
            approved=approved,
            decision_by=chat_id,
            timestamp_ns=now_ns,
        ):
            await query.answer("审批已被其他操作处理", show_alert=True)
            return
        self._repository.record_approval(
            workflow.to_event(),
            approval_mode="manual",
            decision="USER_APPROVED" if approved else "USER_DENIED",
            reason="Telegram user approved" if approved else "Telegram user denied",
            timestamp_ns=now_ns,
        )
        await query.answer("已确认" if approved else "已否决")
        await query.edit_message_text(decided_card(workflow, approved=approved))

    async def _start_polling(self, application: TelegramApplication) -> None:
        self._repository.create_schema()
        self._poll_task = asyncio.create_task(self._poll_mailbox(application))

    async def _poll_mailbox(self, application: TelegramApplication) -> None:
        while True:
            try:
                await self._poll_once(application)
            except Exception:
                LOGGER.exception("Approval mailbox poll failed")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _poll_once(self, application: TelegramApplication) -> None:
        if self._chat_id is None:
            return
        now_ns = time.time_ns()
        expired_ids = self._repository.expire_pending(
            scope=self._workflow_scope,
            timestamp_ns=now_ns,
        )
        for event_id in expired_ids:
            workflow = self._repository.get_signal_workflow(event_id)
            if workflow is not None:
                self._repository.record_approval(
                    workflow.to_event(),
                    approval_mode="manual",
                    decision="EXPIRED",
                    reason="signal expired while awaiting Telegram approval",
                    timestamp_ns=now_ns,
                )
        for workflow in self._repository.list_pending_notifications(scope=self._workflow_scope):
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ 确认", callback_data=f"approve:{workflow.event_id}"
                        ),
                        InlineKeyboardButton("❌ 否决", callback_data=f"deny:{workflow.event_id}"),
                    ]
                ]
            )
            message = await application.bot.send_message(
                chat_id=self._chat_id,
                text=approval_card(workflow),
                reply_markup=keyboard,
            )
            self._repository.mark_workflow_notified(
                workflow.event_id,
                chat_id=self._chat_id,
                message_id=str(message.message_id),
                timestamp_ns=now_ns,
            )
        for workflow_notice in self._repository.list_workflow_alert_notifications(
            scope=self._workflow_scope
        ):
            message = await application.bot.send_message(
                chat_id=self._chat_id,
                text=workflow_alert(workflow_notice.workflow),
            )
            self._repository.mark_telegram_delivered(
                source_key=workflow_notice.source_key,
                chat_id=self._chat_id,
                message_id=str(message.message_id),
                timestamp_ns=now_ns,
            )
        for order_notice in self._repository.list_order_notifications(scope=self._workflow_scope):
            message = await application.bot.send_message(
                chat_id=self._chat_id,
                text=order_notification(order_notice),
            )
            self._repository.mark_telegram_delivered(
                source_key=order_notice.source_key,
                chat_id=self._chat_id,
                message_id=str(message.message_id),
                timestamp_ns=now_ns,
            )

    async def _shutdown(self, _: TelegramApplication) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        self._repository.close()

    def run(self) -> None:
        """运行 Telegram long polling。"""
        self.build_application().run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    """从环境变量启动 Bot。"""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx 的 INFO 请求 URL 会包含 Telegram Bot token, 必须禁止写入运行日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or None
    tws_account = os.getenv("TWS_ACCOUNT", "").strip()
    if not tws_account:
        raise RuntimeError("TWS_ACCOUNT is required")
    project_root = Path(__file__).resolve().parents[3]
    live = load_live_settings(project_root / "config" / "live.yaml")
    ApprovalBot(
        token=token,
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/trading_assistant.db"),
        chat_id=chat_id,
        workflow_scope=f"paper:{tws_account}",
        poll_interval_seconds=live.notification_poll_interval_seconds,
    ).run()


if __name__ == "__main__":
    main()
