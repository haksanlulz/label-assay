"""Daily spend guard — bounds what a public demo can cost.

A publicly reachable app wired to a paid vision API is an open wallet: without a
bound, anyone can run up the bill. Defence is layered. The provider-side
workspace spend cap is the hard ceiling and the thing that actually cannot be
bypassed; this is the app-side bound, so the app degrades politely — a clear
message — instead of silently burning through the cap.

Cost is estimated per label rather than metered from token usage. The estimate is
deliberately conservative, and the provider cap remains the real ceiling; a
production version would meter actual usage from the API response.

State is in-memory and per-process, which matches the single always-on machine
this runs on. It is a cost bound, not an authorization mechanism.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# Conservative estimate for one label: a Haiku vision call with a terse schema.
EST_COST_PER_LABEL_USD = 0.005


class BudgetExhausted(Exception):
    """The day's estimated spend limit has been reached."""


@dataclass
class DailyBudget:
    limit_usd: float
    _day: dt.date | None = field(default=None, repr=False)
    _spent_usd: float = field(default=0.0, repr=False)

    @property
    def spent_usd(self) -> float:
        return self._spent_usd

    def reserve(self, *, today: dt.date | None = None) -> None:
        """Account for one label, or raise if it would exceed today's limit."""
        today = today or dt.date.today()
        if self._day != today:  # a new day resets the tally
            self._day, self._spent_usd = today, 0.0
        if self._spent_usd + EST_COST_PER_LABEL_USD > self.limit_usd:
            raise BudgetExhausted(
                "This server has reached its daily limit for automated label reading. "
                "Please try again tomorrow."
            )
        self._spent_usd += EST_COST_PER_LABEL_USD
