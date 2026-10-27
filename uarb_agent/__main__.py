"""Command line entry point.

    python -m uarb_agent run            # watch the inbox and answer requests
    python -m uarb_agent run --once     # answer what is waiting, then exit
    python -m uarb_agent ask "Hi Agent, can you give me Other Documents files from M12205?"
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .agent import RegulatoryAgent
from .config import Config
from .mailer import Mailbox, build_reply, should_ignore
from .reply import compose_failure, greeting_name

log = logging.getLogger("uarb_agent")


def process_inbox(agent: RegulatoryAgent, mailbox: Mailbox, config: Config) -> int:
    """Answer every unread request once. Returns how many replies were sent."""
    sent = 0
    for mail in mailbox.unread():
        reason = should_ignore(mail, config)
        if reason:
            log.info("Skipping message from %s: %s", mail.sender_address, reason)
            mailbox.mark_read(mail.uid)
            continue
        log.info("Request from %s: %r", mail.sender_address, mail.subject)
        with tempfile.TemporaryDirectory(prefix="uarb-") as tmp:
            try:
                reply = agent.handle(mail.subject, mail.text, mail.sender_name, Path(tmp))
                body, attachment = reply.body, reply.attachment
            except Exception:
                log.exception("Unexpected error while handling a request")
                body, attachment = compose_failure(greeting_name(mail.sender_name), None, config.agent_name), None
            message = build_reply(mail, config.agent_email, config.agent_name, body, attachment)
            try:
                mailbox.send(message)
            except Exception:
                # Leave the request unread so the next poll retries it.
                log.exception("Could not send the reply to %s", mail.sender_address)
                continue
        mailbox.mark_read(mail.uid)
        sent += 1
        log.info("Replied to %s%s", mail.sender_address, " with a ZIP" if attachment else "")
    return sent


def cmd_run(args, config: Config) -> int:
    missing = config.missing_email_settings()
    if missing:
        log.error("Missing email settings: %s (see .env.example)", ", ".join(missing))
        return 2
    agent = RegulatoryAgent(config)
    mailbox = Mailbox(config)
    log.info(
        "Watching %s every %ds (Claude request parsing %s)",
        config.imap_user,
        config.poll_interval,
        f"on, model {config.claude_model}" if config.use_llm else "off",
    )
    while True:
        try:
            process_inbox(agent, mailbox, config)
        except Exception:
            log.exception("Polling the inbox failed")
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(config.poll_interval)


def cmd_ask(args, config: Config) -> int:
    """Handle one request typed on the command line, without email. Saves the ZIP to OUTPUT_DIR."""
    agent = RegulatoryAgent(config)
    with tempfile.TemporaryDirectory(prefix="uarb-") as tmp:
        reply = agent.handle(args.subject, args.text, args.name, Path(tmp))
        print(reply.body)
        if reply.attachment:
            config.output_dir.mkdir(parents=True, exist_ok=True)
            saved = config.output_dir / reply.attachment.name
            shutil.copy(reply.attachment, saved)
            print(f"[ZIP saved to {saved} ({saved.stat().st_size / 1024:.0f} KB)]")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="uarb_agent", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="watch the inbox and answer requests")
    run.add_argument("--once", action="store_true", help="process unread mail once and exit")

    ask = sub.add_parser("ask", help="answer a request typed here instead of emailed")
    ask.add_argument("text", help="the request, e.g. 'Other Documents for M12205'")
    ask.add_argument("--subject", default="", help="optional email subject")
    ask.add_argument("--name", default="User", help="name to greet in the reply")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = Config.from_env()
    if args.command == "run":
        return cmd_run(args, config)
    return cmd_ask(args, config)


if __name__ == "__main__":
    sys.exit(main())
