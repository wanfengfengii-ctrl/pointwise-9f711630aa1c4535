from dataclasses import dataclass


@dataclass(frozen=True)
class AuditionResult:
    frame_count: int


def audition(frames):
    # The input is the normalized, ordered sequence that will be issued for playback.
    return AuditionResult(frame_count=len(frames))
