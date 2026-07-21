"""Telegram Bot 只能变更审批状态的测试。"""

import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from telegram import Update

from trading_assistant.execution.events import TradeSignalEvent
from trading_assistant.notify.bot import ApprovalBot, TelegramApplication, main


def _bot(tmp_path: Path, *, chat_id: str | None = "42") -> ApprovalBot:
    bot = ApprovalBot(
        token="123" + ":test",
        database_url=f"sqlite:///{tmp_path}/bot.db",
        chat_id=chat_id,
        workflow_scope="paper:DU123",
        poll_interval_seconds=0.01,
    )
    bot._repository.create_schema()
    return bot


def _pending(bot: ApprovalBot, *, expires_at_ns: int | None = None) -> str:
    now_ns = time.time_ns()
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key=str(now_ns),
        reason="momentum",
        expires_at_ns=expires_at_ns or now_ns + 3_600_000_000_000,
        ts_event=now_ns,
        ts_init=now_ns,
    )
    workflow, _ = bot._repository.register_signal_workflow(event, scope="paper:DU123")
    bot._repository.prepare_manual_approval(
        workflow.event_id,
        planned_orders=(
            {
                "instrument_id": "SPY.ARCA",
                "side": "BUY",
                "quantity": 1,
                "price": 600.0,
            },
        ),
        risk_summary="risk passed",
        timestamp_ns=now_ns,
    )
    return workflow.event_id


def _callback_update(*, data: str, chat_id: int) -> tuple[Update, Any]:
    query = SimpleNamespace(
        data=data,
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = cast(Update, SimpleNamespace(callback_query=query))
    return update, query


def test_builds_application_with_handlers(tmp_path: Path) -> None:
    application = _bot(tmp_path).build_application()
    assert application.handlers


def test_start_command_reports_chat_id(tmp_path: Path) -> None:
    message = SimpleNamespace(reply_text=AsyncMock())
    update = cast(
        Update,
        SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_message=message),
    )
    asyncio.run(_bot(tmp_path)._start_command(update, cast(Any, None)))
    assert "42" in message.reply_text.await_args.args[0]

    empty = cast(Update, SimpleNamespace(effective_chat=None, effective_message=None))
    asyncio.run(_bot(tmp_path)._start_command(empty, cast(Any, None)))


def test_authorized_callback_approves_once(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    event_id = _pending(bot)
    update, query = _callback_update(data=f"approve:{event_id}", chat_id=42)
    asyncio.run(bot._decision_callback(update, cast(Any, None)))
    assert bot._repository.get_signal_workflow(event_id).status == "APPROVED"  # type: ignore[union-attr]
    assert "已确认" in query.answer.await_args.args[0]
    asyncio.run(bot._decision_callback(update, cast(Any, None)))
    assert "其他操作" in query.answer.await_args_list[-1].args[0]


def test_authorized_callback_denies_without_execution(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    event_id = _pending(bot)
    update, query = _callback_update(data=f"deny:{event_id}", chat_id=42)
    asyncio.run(bot._decision_callback(update, cast(Any, None)))
    assert bot._repository.get_signal_workflow(event_id).status == "DENIED"  # type: ignore[union-attr]
    assert "已否决" in query.answer.await_args.args[0]

    no_query = cast(Update, SimpleNamespace(callback_query=None))
    asyncio.run(bot._decision_callback(no_query, cast(Any, None)))


def test_callback_rejects_wrong_chat_missing_and_expired(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    event_id = _pending(bot)
    wrong, wrong_query = _callback_update(data=f"deny:{event_id}", chat_id=7)
    asyncio.run(bot._decision_callback(wrong, cast(Any, None)))
    assert wrong_query.answer.await_args.kwargs["show_alert"] is True

    missing, missing_query = _callback_update(data="deny:missing", chat_id=42)
    asyncio.run(bot._decision_callback(missing, cast(Any, None)))
    assert "不存在" in missing_query.answer.await_args.args[0]

    expired_id = _pending(bot, expires_at_ns=time.time_ns() - 1)
    expired, expired_query = _callback_update(data=f"deny:{expired_id}", chat_id=42)
    asyncio.run(bot._decision_callback(expired, cast(Any, None)))
    assert "过期" in expired_query.answer.await_args.args[0]


def test_poll_sends_pending_and_order_terminal_once(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    event_id = _pending(bot)
    now_ns = time.time_ns()
    bot._repository.record_order_event(
        signal_event_id=event_id,
        order_event_id="order-event-1",
        timestamp_ns=now_ns,
        strategy_name="dual_momentum",
        instrument_id="SPY.ARCA",
        client_order_id="O-1",
        status="FILLED",
        direction="BUY",
        quantity=1,
        reason="filled",
    )
    backtest_event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("QQQ.NASDAQ", 0.25),),
        rebalance_key="backtest-2026-06",
        reason="historical backtest",
        expires_at_ns=now_ns + 1,
        ts_event=now_ns,
        ts_init=now_ns,
    )
    backtest_workflow, _ = bot._repository.register_signal_workflow(
        backtest_event,
        scope="backtest:run-1",
    )
    bot._repository.record_order_event(
        signal_event_id=backtest_workflow.event_id,
        order_event_id="backtest-order-event",
        timestamp_ns=now_ns,
        strategy_name="dual_momentum",
        instrument_id="QQQ.NASDAQ",
        client_order_id="BACKTEST-O-1",
        status="FILLED",
        direction="BUY",
        quantity=1,
        reason="historical filled",
    )
    send_message = AsyncMock(
        side_effect=[SimpleNamespace(message_id=10), SimpleNamespace(message_id=11)]
    )
    application = cast(
        TelegramApplication,
        SimpleNamespace(bot=SimpleNamespace(send_message=send_message)),
    )
    asyncio.run(bot._poll_once(application))
    assert send_message.await_count == 2
    assert bot._repository.list_pending_notifications(scope="paper:DU123") == ()
    assert bot._repository.list_order_notifications(scope="paper:DU123") == ()
    assert len(bot._repository.list_order_notifications(scope="backtest:run-1")) == 1
    asyncio.run(bot._poll_once(application))
    assert send_message.await_count == 2


def test_poll_sends_risk_rejection_once(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    event = TradeSignalEvent(
        strategy_name="dual_momentum",
        target_weights=(("SPY.ARCA", 0.25),),
        rebalance_key="risk-rejected",
        reason="momentum",
        expires_at_ns=time.time_ns() + 3_600_000_000_000,
        ts_event=time.time_ns(),
        ts_init=time.time_ns(),
    )
    workflow, _ = bot._repository.register_signal_workflow(event, scope="paper:DU123")
    assert bot._repository.reject_new_signal(
        workflow.event_id,
        status="RISK_REJECTED",
        timestamp_ns=time.time_ns(),
        risk_summary="max_order_notional_usd exceeded",
    )
    send_message = AsyncMock(return_value=SimpleNamespace(message_id=12))
    application = cast(
        TelegramApplication,
        SimpleNamespace(bot=SimpleNamespace(send_message=send_message)),
    )
    asyncio.run(bot._poll_once(application))
    assert send_message.await_count == 1
    sent = send_message.await_args
    assert sent is not None
    assert "风控拒绝" in sent.kwargs["text"]
    asyncio.run(bot._poll_once(application))
    assert send_message.await_count == 1


def test_poll_without_chat_and_shutdown_are_safe(tmp_path: Path) -> None:
    bot = _bot(tmp_path, chat_id=None)
    application = cast(TelegramApplication, SimpleNamespace())
    asyncio.run(bot._poll_once(application))
    asyncio.run(bot._shutdown(application))


def test_main_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        main()
    assert logging.getLogger("httpx").level == logging.WARNING
