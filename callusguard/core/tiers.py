"""How reliably does a failure actually repeat?

The stage that stops a count from masquerading as a fact.

    Three failures is not a finding. Three failures out of three attempts is a
    finding; three out of forty is a flake with good PR.

Until now every decision in this loop ran on a bare numerator. `derive` proposed a
rule at `--min-count 3` failures, and `guard prune` promoted a monitor rule at 5
firings. Neither asked the only question that makes those numbers mean anything:
**how many times was it tried?**

The denominator was always in the telemetry — cc-logger records the successes next
to the failures. It simply was never queried.

THE FOUR TIERS

    deterministic   >= 95% of attempts failed, over at least MIN_ATTEMPTS.
                    It has never really worked. Safe to block: there is no
                    legitimate traffic to catch in the crossfire.

    reproducible    >= 50%. Fails most times it is tried. Strong enough to deny,
                    not strong enough to hard-block.

    probabilistic   < 50%. It usually works. Something else — load, a race, a
                    remote — decides. A block here breaks the majority case to
                    catch the minority one, so this tier is capped at a nudge.

    anecdotal       fewer than MIN_ATTEMPTS attempts, at any rate. We have not
                    seen it enough times to say anything. NOT PROPOSED AT ALL.
                    This is the tier that replaces "3 failures = a rule".

    unknown         no denominator available (a caller supplied rows without an
                    attempt count). Not the same as anecdotal: anecdotal means we
                    looked and there was not enough, unknown means we could not
                    look. Capped at a nudge, because absence of evidence is not
                    evidence of a settled failure.

WHAT THE TIER IS, AND WHAT IT IS NOT
    This is an OBSERVATIONAL rate over the command population that happened to
    run — not an experimental one. Nothing here re-ran anything under controlled
    conditions. A `deterministic` tier means "every time you happened to try this,
    in this window, it failed", which is a claim about your traffic, not about the
    command. Read it as a prior, not a proof, and say so wherever it is displayed.

    The honest version of the same caveat, in a neighbouring system, reads
    "deterministic under the observed replay conditions". Same idea; we do not
    even have replay, so the qualifier matters more here, not less.

THE CEILING IS ENFORCED, NOT ADVISORY
    A tier that only decorated a rule would be a comment. `TIER_ACTION_CEILING`
    caps how restrictive an action a tier may carry, and `engine.verify_rules`
    refuses a ruleset that exceeds it — so a `block` derived from a coin-flip
    fails review instead of shipping.
"""

from __future__ import annotations

DETERMINISTIC = "deterministic"
REPRODUCIBLE = "reproducible"
PROBABILISTIC = "probabilistic"
ANECDOTAL = "anecdotal"
UNKNOWN = "unknown"

ALL_TIERS = (DETERMINISTIC, REPRODUCIBLE, PROBABILISTIC, ANECDOTAL, UNKNOWN)

#: Below this many observed attempts, no rate is trustworthy at any value.
MIN_ATTEMPTS = 5

#: Rate boundaries. `>=` on both, so 0.95 is deterministic and 0.50 is reproducible.
DETERMINISTIC_AT = 0.95
REPRODUCIBLE_AT = 0.50

#: The most restrictive action each tier may carry. Enforced by engine.verify_rules.
#:
#: Mirrors engine.ACTION_RANK ordering (monitor < nudge < deny < block). Kept as
#: plain strings here so this module stays import-free and can be read by both the
#: derive side (which sets tiers) and the guard side (which enforces them).
TIER_ACTION_CEILING = {
    DETERMINISTIC: "block",
    REPRODUCIBLE: "deny",
    PROBABILISTIC: "nudge",
    ANECDOTAL: "monitor",
    UNKNOWN: "nudge",
}

#: Tiers strong enough to graduate out of monitor-only. `guard prune` consults this
#: before returning PROMOTE — see the note there on why firing count and failure
#: rate are different measurements.
PROMOTABLE = frozenset({DETERMINISTIC, REPRODUCIBLE})

ONE_LINER = {
    DETERMINISTIC: "failed on effectively every attempt",
    REPRODUCIBLE: "failed on most attempts",
    PROBABILISTIC: "failed on a minority of attempts; usually works",
    ANECDOTAL: "too few attempts to say anything",
    UNKNOWN: "no attempt count available",
}


def classify(fail_count, attempt_count, min_attempts: int = MIN_ATTEMPTS) -> tuple:
    """Return (tier, fail_rate_or_None) for one cluster.

    `attempt_count` is every observed call of the same shape — successes included.
    A falsy/absent attempt_count yields UNKNOWN rather than an invented rate;
    dividing by a denominator nobody supplied is how a count starts impersonating
    a rate.

    A denominator smaller than the numerator is a data defect (clock skew, a
    partial window, a mis-keyed join). We clamp the rate at 1.0 rather than
    reporting 3.0, but we do NOT silently trust the shape: the caller still sees
    both raw numbers in meta and can spot it.
    """
    try:
        fails = int(fail_count or 0)
        attempts = int(attempt_count or 0)
    except (TypeError, ValueError):
        return UNKNOWN, None

    if attempts <= 0:
        return UNKNOWN, None

    rate = min(1.0, fails / attempts) if attempts else None

    if attempts < min_attempts:
        return ANECDOTAL, rate
    if rate >= DETERMINISTIC_AT:
        return DETERMINISTIC, rate
    if rate >= REPRODUCIBLE_AT:
        return REPRODUCIBLE, rate
    return PROBABILISTIC, rate


def ceiling(tier: str) -> str:
    """The most restrictive action `tier` may carry. Unrecognised tiers get the
    strictest cap, not the loosest — an unknown label must never widen authority."""
    return TIER_ACTION_CEILING.get(tier, "monitor")


def describe(tier: str, fail_rate=None, attempt_count=None) -> str:
    """A short human phrase for reports. Always carries the denominator when known,
    because a rate without its sample size is the thing this module exists to stop."""
    text = ONE_LINER.get(tier, tier)
    if fail_rate is not None and attempt_count:
        return "%s (%d%% of %d attempts)" % (text, round(fail_rate * 100), attempt_count)
    return text
