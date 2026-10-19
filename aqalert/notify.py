"""Notifications via Apprise (replaces the Power Automate 'Post to Teams' action).

Apprise is an open-source library that speaks 100+ services through one URL
format, so swapping or adding a channel is a config change, not a code change.

Two channels, each a whitespace/comma-separated list of Apprise URLs held in a
secret / environment variable:

* ``EMAIL_URLS`` — full message with title, e.g.
  ``mailtos://user:app-password@gmail.com?to=you@example.com``
* ``SMS_URLS``   — short message, e.g.
  ``twilio://ACCOUNT_SID:AUTH_TOKEN@+15551230000/+15559870000``
"""

from __future__ import annotations

import logging
import os
import re

import apprise

log = logging.getLogger(__name__)


def _urls(env_name: str) -> list[str]:
    raw = os.environ.get(env_name, "")
    return [u for u in re.split(r"[\s,]+", raw) if u]


class Notifier:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.count = 0  # alerts sent this run
        self.email = apprise.Apprise()
        self.sms = apprise.Apprise()
        for u in _urls("EMAIL_URLS"):
            if not self.email.add(u):
                log.error("Invalid email URL in EMAIL_URLS (check its format)")
        for u in _urls("SMS_URLS"):
            if not self.sms.add(u):
                log.error("Invalid SMS URL in SMS_URLS (check its format)")
        if not dry_run and not (len(self.email) or len(self.sms)):
            log.warning("No EMAIL_URLS or SMS_URLS configured — alerts will only be logged")

    def send(self, title: str, body: str, short: str) -> bool:
        self.count += 1
        if self.dry_run:
            print(f"\n--- EMAIL ---\nSubject: {title}\n\n{body}\n--- SMS ---\n{short}\n")
            return True
        ok = True
        if len(self.email):
            ok &= bool(self.email.notify(title=title, body=body))
        if len(self.sms):
            ok &= bool(self.sms.notify(body=short))
        if not ok:
            log.error("One or more notifications failed to send")
        return ok
