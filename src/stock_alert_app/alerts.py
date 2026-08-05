from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

VERDICT_CHANGE = "verdict_change"
NEW_BULL = "new_bull"
NEW_BEAR = "new_bear"


@dataclass
class Alert:
    kind: str
    market: str
    ticker: str
    verdict: str
    previous: str = ""
    message: str = ""


class Notifier(Protocol):
    def name(self) -> str: ...
    def send(self, alert: Alert) -> None: ...


class ConsoleNotifier:
    def name(self) -> str:
        return "console"

    def send(self, alert: Alert) -> None:
        print(f"[ALERT] {alert.kind}: {alert.market}:{alert.ticker} "
              f"{alert.previous}->{alert.verdict} | {alert.message}")


class EmailNotifier:
    def __init__(self, host: str, port: int, username: str, password: str, to: str, tls: bool = True) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.to = to
        self.tls = tls

    def name(self) -> str:
        return "email"

    def send(self, alert: Alert) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"StockVerdict alert: {alert.market}:{alert.ticker} {alert.previous}->{alert.verdict}"
        msg["From"] = self.username
        msg["To"] = self.to
        msg.set_content(alert.message)
        context = ssl.create_default_context()
        with smtplib.SMTP(self.host, self.port, timeout=20) as server:
            server.starttls(context=context)
            server.login(self.username, self.password)
            server.send_message(msg)


class WebhookNotifier:
    def __init__(self, url: str, token: str = "") -> None:
        self.url = url
        self.token = token

    def name(self) -> str:
        return "webhook"

    def send(self, alert: Alert) -> None:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        httpx.post(
            self.url,
            json={
                "kind": alert.kind,
                "market": alert.market,
                "ticker": alert.ticker,
                "verdict": alert.verdict,
                "previous": alert.previous,
                "message": alert.message,
            },
            headers=headers,
            timeout=15.0,
        )


def build_notifiers() -> list[Notifier]:
    notifiers: list[Notifier] = []
    if os.getenv("ALERT_WEBHOOK_URL"):
        notifiers.append(WebhookNotifier(os.getenv("ALERT_WEBHOOK_URL", ""), os.getenv("ALERT_WEBHOOK_TOKEN", "")))
    if os.getenv("SMTP_HOST"):
        notifiers.append(
            EmailNotifier(
                host=os.getenv("SMTP_HOST", ""),
                port=int(os.getenv("SMTP_PORT", "587")),
                username=os.getenv("SMTP_USER", ""),
                password=os.getenv("SMTP_PASSWORD", ""),
                to=os.getenv("ALERT_TO", ""),
            )
        )
    if not notifiers:
        notifiers.append(ConsoleNotifier())
    return notifiers