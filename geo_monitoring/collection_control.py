"""Conservative, append-only safety controls for Step 3 Web UI collection.

This module schedules interactions; it never opens a browser, sends a prompt,
or attempts to bypass a platform restriction.  Its event log is deliberately
small so a stopped run can be resumed without guessing what already happened.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence


PLATFORM_STOP_STATUSES = frozenset({
    "auth_required", "captcha", "rate_limited", "policy_refusal", "platform_unavailable",
})


def default_risk_policy() -> dict[str, Any]:
    """Return the conservative V1 pacing policy, recorded in each run manifest."""

    return {
        "policy_version": "risk-control-v2.2-conservative",
        "max_global_in_flight": 1,
        "max_platform_in_flight": 1,
        "global_interaction_interval_seconds": 30,
        "platform_interaction_interval_seconds": 120,
        "successful_observations_before_cooldown": 3,
        "success_batch_cooldown_seconds": 600,
        "post_human_recovery_cooldown_seconds": 900,
        "technical_retry_limit": 1,
        "technical_retry_cooldown_seconds": 900,
        "max_open_tabs_per_platform": 1,
        "new_session_per_observation": True,
        "on_platform_stop": "stop_platform_and_require_human_recovery",
    }


def _parse_time(value: str) -> datetime:
    normalized = value
    if len(value) >= 5 and value[-5] in {"+", "-"} and value[-4:].isdigit():
        normalized = f"{value[:-2]}:{value[-2:]}"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("control event timestamps must include a timezone")
    return parsed


def _events_for_platform(events: Sequence[Mapping[str, Any]], platform: str) -> list[Mapping[str, Any]]:
    return [event for event in events if event.get("platform") == platform]


def _latest(events: Sequence[Mapping[str, Any]], event_types: set[str] | None = None) -> Mapping[str, Any] | None:
    candidates = [event for event in events if event.get("at") and (event_types is None or event.get("event_type") in event_types)]
    return max(candidates, key=lambda event: _parse_time(str(event["at"])), default=None)


def _wait(reason: str, until: datetime, *, retry_allowed: bool = False) -> dict[str, Any]:
    return {
        "action": "wait",
        "reason": reason,
        "not_before": until.isoformat(),
        "retry_allowed": retry_allowed,
    }


def next_collection_decision(
    policy: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    platform: str,
    question_id: str,
    now: str,
    global_in_flight: int = 0,
    platform_in_flight: int = 0,
) -> dict[str, Any]:
    """Decide whether one *new* UI interaction is safe to schedule.

    The caller must write a ``prompt_sent`` event before interacting with the
    UI, and a completion/terminal event immediately afterwards.  Returning
    ``send`` does not authorise parallel chats: it is conditional on the caller
    retaining the single-flight lock until the answer/evidence capture ends.
    """

    current = _parse_time(now)
    per_platform = _events_for_platform(events, platform)
    terminal = _latest(per_platform, {"terminal"})
    human_recovery = _latest(per_platform, {"human_recovery_completed"})
    recovery_is_newer = bool(
        terminal and human_recovery
        and _parse_time(str(human_recovery["at"])) > _parse_time(str(terminal["at"]))
    )
    if terminal and terminal.get("terminal_status") in PLATFORM_STOP_STATUSES and not recovery_is_newer:
        return {
            "action": "stop_platform",
            "reason": terminal["terminal_status"],
            "retry_allowed": False,
            "requires_human_recovery": True,
            "terminal_event_id": terminal.get("event_id"),
        }

    if human_recovery:
        recovery_at = _parse_time(str(human_recovery["at"])) + timedelta(
            seconds=int(policy.get("post_human_recovery_cooldown_seconds", 0))
        )
        if current < recovery_at:
            return _wait("post_human_recovery_cooldown", recovery_at)

    if global_in_flight >= int(policy["max_global_in_flight"]):
        return _wait("global_single_flight", current)
    if platform_in_flight >= int(policy["max_platform_in_flight"]):
        return _wait("platform_single_flight", current)

    question_failures = [
        event for event in per_platform
        if event.get("event_type") == "technical_failure" and event.get("question_id") == question_id
    ]
    retry_limit = int(policy["technical_retry_limit"])
    if len(question_failures) > retry_limit:
        return {
            "action": "stop_question",
            "reason": "technical_retry_exhausted",
            "retry_allowed": False,
            "requires_human_recovery": True,
        }
    if question_failures:
        latest_failure = _latest(question_failures)
        assert latest_failure is not None
        retry_at = _parse_time(str(latest_failure["at"])) + timedelta(
            seconds=int(policy["technical_retry_cooldown_seconds"])
        )
        if current < retry_at:
            return _wait("technical_retry_cooldown", retry_at, retry_allowed=True)

    latest_global_send = _latest(events, {"prompt_sent"})
    if latest_global_send:
        global_at = _parse_time(str(latest_global_send["at"])) + timedelta(
            seconds=int(policy["global_interaction_interval_seconds"])
        )
        if current < global_at:
            return _wait("global_interval", global_at, retry_allowed=bool(question_failures))

    latest_platform_send = _latest(per_platform, {"prompt_sent"})
    if latest_platform_send:
        platform_at = _parse_time(str(latest_platform_send["at"])) + timedelta(
            seconds=int(policy["platform_interaction_interval_seconds"])
        )
        if current < platform_at:
            return _wait("platform_interval", platform_at, retry_allowed=bool(question_failures))

    completed = [event for event in per_platform if event.get("event_type") == "observation_completed"]
    batch_size = int(policy["successful_observations_before_cooldown"])
    if completed and len(completed) % batch_size == 0:
        last_completed = _latest(completed)
        assert last_completed is not None
        cooldown_until = _parse_time(str(last_completed["at"])) + timedelta(
            seconds=int(policy["success_batch_cooldown_seconds"])
        )
        if current < cooldown_until:
            return _wait("success_batch_cooldown", cooldown_until, retry_allowed=bool(question_failures))

    return {
        "action": "send",
        "reason": "within_conservative_limits",
        "retry_allowed": bool(question_failures),
        "constraints": {
            "one_open_tab_for_platform": True,
            "new_session_for_observation": bool(policy["new_session_per_observation"]),
            "no_regenerate_or_follow_up": True,
        },
    }


def append_control_event(run_dir: Path, event: Mapping[str, Any]) -> None:
    """Persist one immutable scheduler event for recovery after interruption."""

    required = ("event_id", "event_type", "platform", "at")
    missing = [field for field in required if not event.get(field)]
    if missing:
        raise ValueError(f"control event missing: {', '.join(missing)}")
    _parse_time(str(event["at"]))
    path = run_dir / "control" / "events.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(event), ensure_ascii=False, sort_keys=True) + "\n")


def read_control_events(run_dir: Path) -> list[dict[str, Any]]:
    """Read the append-only event log; an empty run has no control events."""

    path = run_dir / "control" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
