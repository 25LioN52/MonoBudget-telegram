import pytest

from monobudget.classify import Rules
from monobudget.mono import MonoError
from monobudget.render import build_report
from monobudget.sync import MAX_RANGE, RATE_LIMIT, Syncer, SyncState, plan_next, ready_at

from .conftest import FakeMono, fixture, sync_all


def make_syncer(storage, mono, config, limit=200.0):
    return Syncer(storage, mono, config, lambda: limit)


def week_spent(storage, syncer, now, rules):
    p = syncer.periods(now)
    return build_report(storage.transactions(p.history_start, now), p.week, rules, has_previous=True).spent


RULES = Rules(
    excluded_mcc=frozenset({7995}), excluded_descriptions=("tabacos",), category_overrides={"mercadona": "Groceries"}
)


async def test_full_sync_respects_limits_and_skips_non_eur(storage, mono, config, clock):
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    kinds = [c[0].split("/")[0:2] for c in mono.calls]
    assert kinds == [
        ["client-info"],
        ["statement", "eurBlackAcc0001"],  # live window first
        ["statement", "eurWhiteAcc0002"],
        ["statement", "eurBlackAcc0001"],  # then history for "vs previous period"
        ["statement", "eurWhiteAcc0002"],
    ]
    gaps = [b[1] - a[1] for a, b in zip(mono.calls, mono.calls[1:], strict=False)]
    assert all(g >= 60 for g in gaps)
    assert not any("uah" in c[0] or "usd" in c[0] for c in mono.calls)
    assert syncer.synced()
    assert {a.id for a in storage.accounts()} == {"eurBlackAcc0001", "eurWhiteAcc0002"}
    assert week_spent(storage, syncer, clock(), RULES) == 26248


async def test_statement_ranges_never_exceed_31_days(storage, config, clock):
    clock.now = 1793485800  # Sat 31 Oct 2026 23:30 CET (October is 31 days + 1 hour)
    mono = FakeMono(clock, {"eurBlackAcc0001": [], "eurWhiteAcc0002": []})
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    for call, _ in mono.calls:
        if call.startswith("statement"):
            start, end = map(int, call.split("/")[-2:])
            assert end - start <= MAX_RANGE
    assert syncer.has_history(syncer.periods(clock()).month.prev_start)


async def test_rate_limit_survives_restart(storage, mono, config, clock):
    syncer = make_syncer(storage, mono, config)
    await syncer.step(clock())
    clock.advance(30)
    restarted = make_syncer(storage, mono, config)  # new process, same database
    result = await restarted.step(clock())
    assert result.wait_until == clock() - 30 + RATE_LIMIT
    assert len(mono.calls) == 1


def test_wrong_future_clock_does_not_block():
    assert ready_at(10**12, 1000) == 1000
    assert ready_at(0, 1000) == 1000
    assert ready_at(990, 1000) == 990 + RATE_LIMIT


async def test_polls_reconcile_cancelled_holds_and_updated_amounts(storage, config, clock):
    black = fixture("statement-summer-eur-black.json")
    mono = FakeMono(clock, {"eurBlackAcc0001": black, "eurWhiteAcc0002": []})
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    before = week_spent(storage, syncer, clock(), RULES)

    black[:] = [t for t in black if not (t["description"] == "Uber" and t["hold"])]  # hold cancelled
    next(t for t in black if t["description"] == "Repsol")["amount"] = -5500  # settled differently
    clock.advance(config.poll_minutes * 60)
    await sync_all(syncer, clock)
    assert week_spent(storage, syncer, clock(), RULES) == before - 900 - 500


async def test_new_expenses_are_notified_only_after_initial_sync(storage, config, clock):
    black = fixture("statement-summer-eur-black.json")
    mono = FakeMono(clock, {"eurBlackAcc0001": black, "eurWhiteAcc0002": []})
    syncer = make_syncer(storage, mono, config)
    notified = []
    for _ in range(20):
        result = await syncer.step(clock())
        notified += result.notify
        if result.request is None:
            break
        if result.wait_until:
            clock.advance(result.wait_until - clock())
    assert notified == []  # backfill never notifies

    clock.advance(config.poll_minutes * 60)
    black.append({**black[0], "id": "fakeNew0001", "time": clock() - 30, "description": "Lidl", "amount": -1234})
    black.append({**black[0], "id": "fakeNew0002", "time": clock() - 20, "description": "Refund", "amount": 500})
    results = []
    for _ in range(5):
        r = await syncer.step(clock())
        results.append(r)
        if r.request is None:
            break
        if r.wait_until:
            clock.advance(r.wait_until - clock())
    new = [t.id for r in results for t in r.notify]
    assert new == ["fakeNew0001"]  # expenses only, once


async def test_pages_backwards_at_500_items(storage, config, clock):
    now = clock()
    many = [
        {
            "id": f"bulk{i}",
            "time": now - 60 - i * 120,
            "description": "Shop",
            "mcc": 5411,
            "originalMcc": 5411,
            "hold": False,
            "amount": -100,
            "currencyCode": 978,
        }
        for i in range(1200)
    ]
    mono = FakeMono(clock, {"eurBlackAcc0001": many, "eurWhiteAcc0002": []})
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    assert len(storage.transactions()) == 1200
    assert sum(1 for c, _ in mono.calls if "eurBlackAcc0001" in c) >= 3


@pytest.mark.parametrize("kind", ["rate_limit", "network", "http", "auth"])
async def test_errors_keep_the_cache(storage, mono, config, clock, kind):
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    snapshot = [t.id for t in storage.transactions()]
    clock.advance(config.poll_minutes * 60)
    mono.fail_with = MonoError(kind, 500)
    result = await syncer.step(clock())
    assert result.error == kind
    assert [t.id for t in storage.transactions()] == snapshot
    assert storage.get("last_error")["kind"] == kind


async def test_unexpected_shapes_never_wipe_data(storage, mono, config, clock):
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    snapshot = [t.id for t in storage.transactions()]
    clock.advance(config.poll_minutes * 60)

    async def broken(*_args):
        return [{"id": 1, "broken": True}]

    mono.statement = broken
    result = await syncer.step(clock())
    assert result.error == "parse"
    assert [t.id for t in storage.transactions()] == snapshot

    async def empty_info():
        return {"accounts": []}

    storage.set("client_info_at", 1)  # force a client-info refresh
    mono.client_info = empty_info
    clock.advance(RATE_LIMIT)
    for account in storage.accounts():
        storage.update_account(account.id, polled_at=clock(), reconciled_at=clock())
    result = await syncer.step(clock())
    assert result.error == "parse"
    assert len(storage.accounts()) == 2


async def test_account_ids_narrow_selection_and_drop_data(storage, mono, config, clock, tmp_path):
    from .conftest import make_config

    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    narrowed = make_config(tmp_path, ACCOUNT_IDS="eurBlackAcc0001")
    syncer = make_syncer(storage, mono, narrowed)
    storage.set("client_info_at", clock() - 2 * 86400)
    clock.advance(config.poll_minutes * 60)
    await sync_all(syncer, clock)
    assert [a.id for a in storage.accounts()] == ["eurBlackAcc0001"]
    assert all(t.account_id == "eurBlackAcc0001" for t in storage.transactions())


async def test_refresh_forces_a_poll(storage, mono, config, clock):
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    clock.advance(70)
    assert (await syncer.step(clock())).request is None
    syncer.request_refresh(clock())
    request, _ = syncer.next_request(clock())
    assert request.mode == "poll"


def test_poll_window_covers_gap_after_downtime(storage, mono, config, clock):
    state = SyncState(client_info_at=clock(), accounts=[])
    assert plan_next(state, clock(), make_syncer(storage, mono, config).periods(clock()), 5, 0) is None


async def test_malformed_client_info_keeps_accounts_and_data(storage, mono, config, clock):
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    snapshot = ([a.id for a in storage.accounts()], [t.id for t in storage.transactions()])
    for broken in ({"currencyCode": "978"}, {"id": "../x"}, {"iban": 42}):
        info = fixture("client-info.json")
        info["accounts"][0].update(broken)

        async def changed(info=info):
            return info

        mono.client_info = changed
        storage.set("client_info_at", 1)  # force a refresh
        clock.advance(RATE_LIMIT)
        for account in storage.accounts():
            storage.update_account(account.id, polled_at=clock(), reconciled_at=clock())
        result = await syncer.step(clock())
        assert result.error == "parse", broken
        assert ([a.id for a in storage.accounts()], [t.id for t in storage.transactions()]) == snapshot
        assert storage.get("no_eur_accounts") is not True


async def test_polls_never_starve_history_or_reconcile(storage, mono, tmp_path, clock):
    from .conftest import make_config

    config = make_config(tmp_path, POLL_MINUTES="2")  # 2 accounts x 61 s >= 2 min
    syncer = make_syncer(storage, mono, config)
    modes = []
    end = clock() + 3 * 3600  # three hours, stepping like Service.tick does
    while clock() < end:
        result = await syncer.step(clock())
        if result.wait_until:
            clock.advance(result.wait_until - clock())
        elif result.request is None:
            clock.advance(60)
        else:
            modes.append(result.request.mode)
            clock.advance(1)
    assert syncer.has_history(syncer.periods(clock()).history_start)
    assert modes.count("reconcile") >= 4  # roughly hourly per account
    assert modes.count("poll") > 50


async def test_own_transfers_detected_via_hashed_ibans(storage, mono, config, clock):
    syncer = make_syncer(storage, mono, config)
    await sync_all(syncer, clock)
    own = [t for t in storage.transactions() if t.description == "Переказ на картку"]
    assert own and own[0].own is True
    dump = "\n".join(storage.db.iterdump())
    for account in fixture("client-info.json")["accounts"]:
        assert account["iban"] not in dump
