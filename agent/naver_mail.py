"""Sends mail from the owner's Naver address.

SMTP is used when an app password exists. Otherwise the owner's browser session writes the mail.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .authgate import SITES, AuthBlocked
from .loop import Tool

SMTP_HOST = "smtp.naver.com"
SMTP_PORT = 587
COMPOSE_URL = "https://mail.naver.com/v2/new"


def owner_address(env) -> str:
    user = env["NAVER_ID"]
    return user if "@" in user else f"{user}@naver.com"


def build_message(env, to: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = owner_address(env)
    message["To"] = to or owner_address(env)
    message["Subject"] = subject
    message.set_content(body)
    return message


def send_smtp(env, message: EmailMessage, smtp_factory=smtplib.SMTP) -> None:
    with smtp_factory(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(owner_address(env), env["NAVER_APP_PASSWORD"])
        smtp.send_message(message)


def send_browser(gate, browser, message: EmailMessage) -> None:
    gate.ensure("naver")
    page = browser.open(SITES["naver"])
    try:
        page.goto(COMPOSE_URL)
        page.compose_mail(message["To"], message["Subject"], message.get_content().strip())
        browser.save(SITES["naver"])
    finally:
        browser.close()


def build_mail_tool(gate, browser, env, smtp_factory=smtplib.SMTP) -> Tool:
    def run(args: dict) -> str:
        message = build_message(env, args.get("to", ""), args["subject"], args["body"])
        try:
            if env.get("NAVER_APP_PASSWORD"):
                send_smtp(env, message, smtp_factory)
            else:
                send_browser(gate, browser, message)
        except AuthBlocked as error:
            return f"blocked: {error}"
        return f"sent from {message['From']} to {message['To']}"

    return Tool(
        "mail_send",
        "사용자 본인의 네이버 메일 주소로 메일을 보낸다. to를 비우면 나에게 쓰기다. 사용자가 요청한 경우에만 쓴다.",
        {
            "type": "object",
            "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
            "required": ["subject", "body"],
        },
        run,
    )
