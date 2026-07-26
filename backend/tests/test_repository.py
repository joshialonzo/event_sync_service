"""Tests for the repository (step 14).

The interesting claim here is doc 03's: a re-sync while the UI is open cannot produce a torn
read. That is a concurrency claim, so it is tested with real threads rather than by reading
the implementation and agreeing with it.
"""

import threading
from datetime import date, datetime, timezone

from app.models.unified import (
    Origin,
    SyncResult,
    SyncRunSummary,
    UnifiedMeeting,
)
from app.repository import Repository
from app.repository.memory import EMPTY, InMemoryRepository


def _meeting(index: int) -> UnifiedMeeting:
    return UnifiedMeeting(
        id=f"m-{index}",
        origin=Origin.CRM_ONLY,
        crm_ids=[f"CRM-{index}"],
        event_date=date(2025, 3, 1 + (index % 28)),
    )


def _result(count: int) -> SyncResult:
    meetings = {f"m-{i}": _meeting(i) for i in range(count)}
    return SyncResult(
        meetings=meetings,
        by_date=list(meetings),
        summary=SyncRunSummary(
            generated_at=datetime.now(tz=timezone.utc), meetings_out=count
        ),
    )


# --- the contract ---


def test_in_memory_repository_satisfies_the_protocol() -> None:
    """Structurally — `InMemoryRepository` neither imports nor inherits `Repository`."""
    assert isinstance(InMemoryRepository(), Repository)


def test_a_fresh_repository_is_empty_not_broken() -> None:
    """Modelling "not loaded yet" as None would push an `if store is None` branch into every
    route and template."""
    repository = InMemoryRepository()

    assert repository.list_meetings() == []
    assert repository.get_meeting("anything") is None
    assert repository.get_stats().meetings_out == 0


def test_list_meetings_follows_by_date_not_dict_order() -> None:
    """The list view renders this directly, so ordering is the store's responsibility."""
    meetings = {"m-2": _meeting(2), "m-1": _meeting(1), "m-3": _meeting(3)}
    result = SyncResult(
        meetings=meetings,
        by_date=["m-1", "m-2", "m-3"],
        summary=SyncRunSummary(generated_at=datetime.now(tz=timezone.utc)),
    )

    repository = InMemoryRepository(result)

    assert [m.id for m in repository.list_meetings()] == ["m-1", "m-2", "m-3"]


def test_get_meeting_returns_none_for_an_unknown_id() -> None:
    """"Not found" is an ordinary answer here; the route turns it into a 404."""
    repository = InMemoryRepository(_result(3))

    assert repository.get_meeting("m-1") is not None
    assert repository.get_meeting("ghost") is None


def test_get_stats_returns_the_current_summary() -> None:
    repository = InMemoryRepository(_result(24))

    assert repository.get_stats().meetings_out == 24


# --- replacement ---


def test_replace_all_publishes_the_new_dataset() -> None:
    repository = InMemoryRepository()

    repository.replace_all(_result(24))

    assert len(repository.list_meetings()) == 24


def test_replacing_twice_leaves_only_the_latest() -> None:
    """A re-sync is a replacement, not a merge — stale meetings must not survive it."""
    repository = InMemoryRepository()

    repository.replace_all(_result(24))
    repository.replace_all(_result(2))

    assert len(repository.list_meetings()) == 2
    assert repository.get_meeting("m-5") is None
    assert repository.get_stats().meetings_out == 2


def test_replace_all_does_not_copy_or_mutate_the_result() -> None:
    """The result is frozen and already validated; copying it would cost the atomicity
    argument nothing and gain nothing."""
    result = _result(4)

    repository = InMemoryRepository()
    repository.replace_all(result)

    assert repository.result is result


def test_a_held_reference_is_unaffected_by_a_later_swap() -> None:
    """A request that has already read the store completes against the dataset it started
    with, rather than seeing meetings vanish mid-render."""
    repository = InMemoryRepository(_result(24))

    snapshot = repository.list_meetings()
    repository.replace_all(_result(2))

    assert len(snapshot) == 24
    assert len(repository.list_meetings()) == 2


# --- the atomicity claim, tested with threads ---


def test_readers_never_observe_a_half_written_store() -> None:
    """Doc 03's claim: a reader sees the entire previous dataset or the entire new one.

    A writer alternates between a 24-meeting and a 2-meeting result while readers check that
    what they see is *self-consistent* — the list length agrees with the summary read
    alongside it, and every listed meeting is retrievable by id. An implementation that
    cleared a dict and refilled it would fail here: a meeting would appear in the list and
    404 on lookup.
    """
    repository = InMemoryRepository(_result(24))
    big, small = _result(24), _result(2)
    stop = threading.Event()
    inconsistencies: list[str] = []

    def writer() -> None:
        toggle = True
        while not stop.is_set():
            repository.replace_all(big if toggle else small)
            toggle = not toggle

    def reader() -> None:
        while not stop.is_set():
            snapshot = repository.result
            meetings = snapshot.ordered_meetings
            summary = snapshot.summary

            if len(meetings) != summary.meetings_out:
                inconsistencies.append(f"{len(meetings)} listed vs {summary.meetings_out}")

            for meeting in meetings:
                if snapshot.meetings.get(meeting.id) is None:
                    inconsistencies.append(f"{meeting.id} listed but not retrievable")

    threads = [threading.Thread(target=writer)] + [
        threading.Thread(target=reader) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    stop.wait(0.4)
    stop.set()
    for thread in threads:
        thread.join(timeout=2)

    assert inconsistencies == []


def test_concurrent_readers_see_one_size_or_the_other() -> None:
    """The looser but more direct form of the same claim: never a mixture."""
    repository = InMemoryRepository(_result(24))
    sizes: set[int] = set()
    stop = threading.Event()

    def writer() -> None:
        toggle = True
        while not stop.is_set():
            repository.replace_all(_result(24) if toggle else _result(2))
            toggle = not toggle

    def reader() -> None:
        while not stop.is_set():
            sizes.add(len(repository.list_meetings()))

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    stop.wait(0.3)
    stop.set()
    for thread in threads:
        thread.join(timeout=2)

    assert sizes <= {24, 2}, f"observed a partial dataset: {sizes}"


def test_the_empty_constant_is_a_valid_result() -> None:
    """EMPTY passes SyncResult's own validator — by_date permutes meetings, trivially."""
    assert EMPTY.meetings == {}
    assert EMPTY.by_date == []
    assert EMPTY.ordered_meetings == []
