"""Outgoing email. The provider is a decision (docs/PROVIDERS.md); this is the interface and a sandbox that only remembers what it was given.

A request never sends mail: it queues a `notifications` row and a `mail.send` job, and the worker calls the provider (`worker.handle_mail_send`),
like every other call out of the system. `BIZ_MAIL_PROVIDER` picks the sender; the start-up guard refuses `sandbox` in production.
"""
from __future__ import annotations

import os
from typing import Protocol


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class SandboxMailer:
    """Keeps what would have been sent, so a test or a developer can read it. It delivers nothing."""
    sent: list[tuple[str, str, str]] = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))

    @classmethod
    def last(cls, to: str) -> tuple[str, str] | None:
        for t, s, b in reversed(cls.sent):
            if t == to:
                return s, b
        return None


MAILERS: dict[str, type] = {"sandbox": SandboxMailer}
_active: dict[str, object] = {}


def register_mailer(name: str, cls: type) -> None:
    MAILERS[name] = cls


def provider_name() -> str:
    return os.environ.get("BIZ_MAIL_PROVIDER", "sandbox")


def mailer() -> Mailer:
    if "m" not in _active:
        name = provider_name()
        if name not in MAILERS:
            raise RuntimeError(f"BIZ_MAIL_PROVIDER selects '{name}', but only {sorted(MAILERS)} are installed")
        _active["m"] = MAILERS[name]()
    return _active["m"]


def web_base_url() -> str:
    return os.environ.get("BIZ_WEB_URL", "http://localhost:3000").rstrip("/")


def invitation(company: str, full_name: str, email: str) -> tuple[str, str]:
    """The subject and body of the invitation to a person's own employee page. It holds no secret: they sign in with a one-time code sent to this address."""
    subject = f"{company} has invited you to your employee page"
    body = (f"Hello {full_name},\n\n{company} has opened your employee page on Biz360X. There you can see your payslips and leave, apply for leave, "
            f"read the handbook and keep your details up to date.\n\nSign in at {web_base_url()}/signin with this email address ({email}). "
            f"You will be sent a one-time code; then choose \"Open my employee page\".\n\nIf you did not expect this, you can ignore this email.\n")
    return subject, body
