def parse_duration(s: str) -> int:
    """Parse a duration string like '1h30m', '45s', '2h', '90m10s' into total seconds.

    Rules:
    - Units: h (hours), m (minutes), s (seconds). Units must appear in h->m->s order.
    - Each unit may appear at most once. At least one unit is required.
    - Whitespace is not allowed. Raises ValueError on any invalid input.
    - Values are non-negative integers (no sign, no decimals).
    """
    import re
    if not isinstance(s, str):
        raise ValueError("input must be str")
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s)
    if not m or not any(m.groups()):
        raise ValueError(f"invalid duration: {s!r}")
    h, mi, se = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + se
