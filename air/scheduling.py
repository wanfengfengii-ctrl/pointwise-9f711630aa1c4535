from dataclasses import dataclass

from air.obligations import Obligation


@dataclass(frozen=True)
class Placement:
    obligation: Obligation
    start: int
    end: int


def _candidate_bits(mask):
    while mask:
        bit = mask & -mask
        yield bit
        mask ^= bit


def find_schedule(obligations, first_free):
    items = sorted(obligations, key=lambda item: (item.start, item.end, item.request_id))
    if not items:
        return []

    # For a fixed playback order the earliest start of each clip dominates every
    # later start. Searching orders therefore covers all feasible placements.
    remaining = (1 << len(items)) - 1
    stack = [(first_free, remaining, _candidate_bits(remaining))]
    placed = []
    impossible_from = {}
    while stack:
        free_at, remaining, candidates = stack[-1]
        bit = next(candidates, 0)
        if not bit:
            impossible_from[remaining] = min(free_at, impossible_from.get(remaining, free_at))
            stack.pop()
            if placed:
                placed.pop()
            continue

        item = items[bit.bit_length() - 1]
        start = max(free_at, item.start)
        end = start + item.duration
        if end > item.end:
            continue
        later = remaining ^ bit
        if later in impossible_from and end >= impossible_from[later]:
            continue
        placement = Placement(item, start, end)
        if not later:
            return [*placed, placement]
        placed.append(placement)
        stack.append((end, later, _candidate_bits(later)))
    return None
