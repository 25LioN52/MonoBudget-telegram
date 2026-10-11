from monobudget.service import Service
from monobudget.sync import Syncer

from .conftest import FakeMono, fixture, make_config, ts

CHAT = 111


async def synced(service: Service, clock) -> Service:
    service.register_chat(CHAT)
    for _ in range(30):
        await service.tick()
        request, ready = service.syncer.next_request(clock())
        if request is None:
            break
        clock.now = max(clock.now, ready)
    return service


async def test_status_message_is_created_pinned_and_edited_only_on_change(service, messenger, clock):
    await synced(service, clock)
    status_id = service.storage.get(f"status:{CHAT}")["message_id"]
    assert status_id in messenger.pinned
    assert "<pre>" in messenger.messages[status_id]["text"]
    edits = messenger.edits
    await service.update_status(CHAT)
    assert messenger.edits == edits  # unchanged text: no API call at all


async def test_status_is_recreated_and_repinned_when_deleted(service, messenger, clock):
    await synced(service, clock)
    old = service.storage.get(f"status:{CHAT}")["message_id"]
    del messenger.messages[old]  # user deleted it
    service.storage.set(f"status:{CHAT}", {"message_id": old, "digest": "stale"})
    await service.update_status(CHAT)
    new = service.storage.get(f"status:{CHAT}")["message_id"]
    assert new != old and new in messenger.pinned


async def test_expense_notifications_once_and_only_for_new_expenses(tmp_path, storage, messenger, clock):
    black = fixture("statement-summer-eur-black.json")
    mono = FakeMono(clock, {"eurBlackAcc0001": black, "eurWhiteAcc0002": []})
    config = make_config(tmp_path)
    service = Service(config, storage, lambda s: Syncer(storage, mono, config, s.weekly_limit), messenger, clock)
    await synced(service, clock)
    texts = [text for _, text in messenger.sent]
    assert not any("Lidl Nuevo" in t for t in texts)

    clock.advance(config.poll_minutes * 60)
    black.append(
        {
            **black[0],
            "id": "fakeNew0001",
            "time": clock() - 30,
            "description": "Lidl Nuevo",
            "amount": -1240,
            "mcc": 5411,
        }
    )
    await synced(service, clock)
    await synced(service, clock)
    hits = [t for _, t in messenger.sent if "Lidl Nuevo" in t]
    assert len(hits) == 1
    assert chr(0x2212) + "12,40" in hits[0] and "Продукти" in hits[0]


async def test_notifications_can_be_disabled(tmp_path, storage, messenger, clock):
    black = fixture("statement-summer-eur-black.json")
    mono = FakeMono(clock, {"eurBlackAcc0001": black, "eurWhiteAcc0002": []})
    config = make_config(tmp_path, NOTIFY_EACH_EXPENSE="false")
    service = Service(config, storage, lambda s: Syncer(storage, mono, config, s.weekly_limit), messenger, clock)
    await synced(service, clock)
    clock.advance(config.poll_minutes * 60)
    black.append({**black[0], "id": "fakeNew0002", "time": clock() - 30, "description": "Quiet", "amount": -100})
    await synced(service, clock)
    assert not any("Quiet" in t for _, t in messenger.sent)


async def test_alerts_are_sent_once_per_period(service, messenger, clock):
    await synced(service, clock)  # fixture week: 262 € of 200 € -> over the limit
    alerts = [t for _, t in messenger.sent if "🔴" in t or "⚠️ Тиждень" in t]
    assert len([a for a in alerts if "Тиждень" in a]) == 1
    await service.check_alerts(clock())
    assert [t for _, t in messenger.sent if "🔴" in t or "⚠️ Тиждень" in t] == alerts


async def test_period_end_report_on_monday_morning(tmp_path, storage, messenger, mono, clock):
    config = make_config(tmp_path, REPORT_ON_PERIOD_END="true")
    service = Service(config, storage, lambda s: Syncer(storage, mono, config, s.weekly_limit), messenger, clock)
    await synced(service, clock)
    await service.period_end_reports(clock())
    assert not any("Звіт:" in t for _, t in messenger.sent)  # first run: nothing retroactive

    clock.now = ts("2026-07-06T06:30:00+00:00")  # Mon 08:30: too early
    await synced(service, clock)
    await service.period_end_reports(clock())
    assert not any("Звіт: Минулий тиждень" in t for _, t in messenger.sent)

    clock.now = ts("2026-07-06T07:05:00+00:00")  # Mon 09:05
    await synced(service, clock)
    await service.period_end_reports(clock())
    await service.period_end_reports(clock())
    assert len([t for _, t in messenger.sent if "Звіт: Минулий тиждень" in t]) == 1


async def test_report_message_replaces_the_previous_one(service, messenger, clock):
    await synced(service, clock)
    await service.send_report(CHAT, "w")
    first = service.storage.get(f"report:{CHAT}")
    await service.send_report(CHAT, "m")
    assert first in messenger.deleted
    assert "Місяць" in messenger.messages[service.storage.get(f"report:{CHAT}")]["text"]


async def test_category_drilldown_and_all_transactions(service, clock):
    await synced(service, clock)
    text, keyboard = service.transactions("w", "gro", 0, clock())
    assert "Продукти" in text and "MERCADONA" in text
    assert keyboard[-1] == [("⬅️ До звіту", "r:w")]
    text, _ = service.transactions("w", None, 0, clock())
    assert "<s>" in text  # excluded transactions shown struck through


def test_setlimit_validation_and_reset(service):
    assert "Тижневий ліміт: 150,00" in service.set_limit("150")
    assert service.weekly_limit() == 150
    assert "від 1 до" in service.set_limit("-5")
    assert "від 1 до" in service.set_limit("abc")
    assert "від 1 до" in service.set_limit("1e9")
    assert "скинуто" in service.set_limit("reset")
    assert service.weekly_limit() == 200


def test_category_rules(service):
    assert "«mercadona» → Продукти" in service.add_category("Mercadona = Продукти")
    assert "«starbucks» → Кава" in service.add_category("starbucks Кава")
    assert "від 2 до 60" in service.add_category("x = Продукти")
    text, keyboard = service.categories_view()
    assert "mercadona" in text and len(keyboard) == 2
    override_id = int(keyboard[0][0][1].split(":")[1])
    service.delete_category(override_id)
    assert len(service.storage.overrides()) == 1
    assert service.rules().category_overrides == {"starbucks": "Кава"}


def test_settings_never_show_tokens(service, config):
    text = service.settings_text()
    for secret in config.secrets:
        assert secret not in text


async def test_refresh_answer_respects_rate_limit(service, clock):
    await synced(service, clock)
    assert service.refresh().startswith("Наступний запит") or service.refresh() == "Оновлюю…"
    assert service.wake.is_set()


async def test_tick_backs_off_on_network_errors(service, mono, clock):
    await synced(service, clock)
    from monobudget.mono import MonoError

    mono.fail_with = MonoError("network")
    clock.advance(10 * 60)
    backoffs = []
    for _ in range(8):
        sleep = await service.tick()
        if sleep > 1:
            backoffs.append(sleep)
        clock.advance(sleep)
    assert backoffs[:3] == [60, 120, 240]  # exponential, capped at 15 min


async def test_auth_error_is_reported_once(service, mono, messenger, clock):
    await synced(service, clock)
    from monobudget.mono import MonoError

    mono.fail_with = MonoError("auth", 401)
    for _ in range(3):
        clock.advance(31 * 60)
        await service.tick()
    assert len([t for _, t in messenger.sent if "не приймає токен" in t]) == 1


async def test_alerts_use_exact_amounts(service, messenger, clock):
    from monobudget.render import build_report

    await synced(service, clock)
    service.storage.set("weekly_limit", 262.49)  # fixture week spent: 262.48 € -> 99.996 %
    service.storage.delete("alerts")
    messenger.sent.clear()
    await service.check_alerts(clock())
    texts = [t for _, t in messenger.sent]
    assert not any("🔴" in t for t in texts) and any("⚠️" in t for t in texts)
    week = service.reports(clock())[0]
    assert week.pct == 99  # never shown as 100 % before the limit is reached
    assert isinstance(build_report, object)


def test_category_rules_are_paginated(service):
    for i in range(25):
        service.add_category(f"shop{i:02d} = Покупки")
    text, keyboard = service.categories_view(0)
    assert "стор. 1/3" in text and len(keyboard) == 11  # 10 rules + navigation
    assert keyboard[-1] == [("▶", "k:1")]
    text, keyboard = service.categories_view(2)
    assert "стор. 3/3" in text and keyboard[-1] == [("◀", "k:1")]
    assert all(len(d.encode()) <= 64 for row in keyboard for _, d in row)


async def test_heartbeat_stays_fresh_during_long_waits(service, monkeypatch, tmp_path):
    import asyncio

    from monobudget import service as service_module

    monkeypatch.setattr(service_module, "HEARTBEAT_EVERY", 0.01)
    beats = []
    monkeypatch.setattr(service, "heartbeat", lambda: beats.append(1))
    await service._sleep(0.05, asyncio.Event())
    assert len(beats) >= 3
