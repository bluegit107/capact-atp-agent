from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import Frozen, Provenance, Role


class ReplyOutcome(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    COUNTER = "COUNTER"
    CONDITIONAL = "CONDITIONAL"
    NO_REPLY = "NO_REPLY"


class Reply(Frozen):
    reply_id: str = Field(pattern=r"^RP-\d{3,}$")
    request_id: str = Field(pattern=r"^IR-\d{3,}$")
    raw_text: str
    outcome: ReplyOutcome
    terms: dict = {}
    provenance: Literal[Provenance.SIMULATED, Provenance.HUMAN_INPUT]
    parse_confidence: float = Field(default=1.0, ge=0, le=1)
    human_confirmed: bool = False

    @model_validator(mode="after")
    def _terms_required(self) -> "Reply":
        if self.outcome in (ReplyOutcome.COUNTER, ReplyOutcome.CONDITIONAL) and not self.terms:
            raise ValueError(f"{self.outcome}: 조건/역제안 수치(terms)가 필요하다")
        return self

    @property
    def usable_for_replan(self) -> bool:
        """구조화 결과를 재계획에 쓰려면 사람이 확인했거나 파싱 신뢰도가 충분해야 한다."""
        return self.human_confirmed or self.provenance is Provenance.SIMULATED or self.parse_confidence >= 0.9


class InfoRequestStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    REPLIED = "REPLIED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


class InfoRequest(Frozen):
    request_id: str = Field(pattern=r"^IR-\d{3,}$")
    to_role: Role
    counterparty: str
    question: str
    answer_by: date
    linked_assumption_id: str | None = Field(default=None, pattern=r"^AS-\d{3}$")
    linked_lever_id: str | None = Field(default=None, pattern=r"^LV-\d{3,}$")
    status: InfoRequestStatus = InfoRequestStatus.DRAFT
    draft_by_llm: bool = False

    @model_validator(mode="after")
    def _linked(self) -> "InfoRequest":
        if not (self.linked_assumption_id or self.linked_lever_id):
            raise ValueError("문의는 가정 또는 레버에 연결되어야 한다 (목적 없는 문의 금지)")
        return self

    def send(self) -> "InfoRequest":
        if self.status is not InfoRequestStatus.DRAFT:
            raise ValueError(f"{self.request_id}: DRAFT 만 발송 가능 ({self.status})")
        return self.model_copy(update={"status": InfoRequestStatus.SENT})

    def receive(self, reply: Reply) -> "InfoRequest":
        if self.status is not InfoRequestStatus.SENT:
            raise ValueError(f"{self.request_id}: 발송되지 않은 문의에 회신 불가 ({self.status})")
        if reply.request_id != self.request_id:
            raise ValueError("회신이 다른 문의에 속함")
        return self.model_copy(update={"status": InfoRequestStatus.REPLIED})

    def expire_if_due(self, today: date) -> "InfoRequest":
        """E24: 기한까지 회신이 없으면 만료 처리 -> 재계획 트리거."""
        if self.status is InfoRequestStatus.SENT and today > self.answer_by:
            return self.model_copy(update={"status": InfoRequestStatus.EXPIRED})
        return self

    def reask(self, answer_by: date) -> "InfoRequest":
        """무응답 만료된 문의를 새 기한으로 재문의(같은 문의 ID, 이력은 감사 로그에)."""
        if self.status is not InfoRequestStatus.EXPIRED:
            raise ValueError(f"{self.request_id}: 만료된 문의만 재문의 가능 ({self.status})")
        return self.model_copy(update={"status": InfoRequestStatus.SENT, "answer_by": answer_by})

    def close(self) -> "InfoRequest":
        if self.status not in (InfoRequestStatus.REPLIED, InfoRequestStatus.EXPIRED):
            raise ValueError("회신/만료 전에는 종료할 수 없다")
        return self.model_copy(update={"status": InfoRequestStatus.CLOSED})


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    WAITING_REPLY = "WAITING_REPLY"
    DONE = "DONE"
    BLOCKED = "BLOCKED"


_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.OPEN: frozenset({TaskStatus.WAITING_REPLY, TaskStatus.DONE, TaskStatus.BLOCKED}),
    TaskStatus.WAITING_REPLY: frozenset({TaskStatus.OPEN, TaskStatus.DONE, TaskStatus.BLOCKED}),
    TaskStatus.BLOCKED: frozenset({TaskStatus.OPEN}),
    TaskStatus.DONE: frozenset(),
}


class Task(Frozen):
    task_id: str = Field(pattern=r"^TK-\d{3,}$")
    title: str
    owner_role: Role
    status: TaskStatus = TaskStatus.OPEN
    due: date | None = None
    linked_request_id: str | None = None
    linked_approval_id: str | None = None
    blocked_reason: str | None = None

    def move(self, dst: TaskStatus, blocked_reason: str | None = None) -> "Task":
        if dst not in _TASK_TRANSITIONS[self.status]:
            raise ValueError(f"{self.task_id}: {self.status} -> {dst} 불가")
        if dst is TaskStatus.BLOCKED and not blocked_reason:
            raise ValueError("BLOCKED 는 사유가 필요하다")
        return self.model_copy(update={"status": dst, "blocked_reason": blocked_reason if dst is TaskStatus.BLOCKED else None})
