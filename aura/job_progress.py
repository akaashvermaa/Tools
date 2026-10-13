"""
Turns yt-dlp's raw progress callbacks into numbers that are safe to show.

yt-dlp reports each stream (video, then audio, ...) separately, restarts at 0% for
every one, re-estimates sizes while fragments arrive, and gives an instantaneous
speed/ETA that jumps around. Shown as-is the bar runs backwards and the timer
flickers. This tracker combines the streams of a video and the videos of a playlist
into one overall fraction that only moves forward, and smooths speed and time left.
"""

import time
from collections import deque
from typing import Callable, Optional

# Share of an item's progress used by the download itself. The rest is left for
# merging / converting with ffmpeg, which yt-dlp doesn't report progress for.
DOWNLOAD_SHARE = 0.97

SPEED_SMOOTHING = 0.2   # weight of the newest speed reading (exponential moving average)
ETA_SMOOTHING = 0.2     # same for the time-left estimate
SAMPLE_EVERY = 0.5      # seconds between progress samples used for the ETA
SINGLE_WINDOW = 15.0    # a single video: judge the rate over the last N seconds
MIN_SPAN = 3.0          # need at least N seconds of samples before showing an ETA
STALE_AFTER = 4.0       # no download data for N seconds -> speed is unknown (e.g. pausing)


def stream_fraction(d: dict, expected_size: Optional[float] = None) -> float:
    """How far along one stream is, 0..1, preferring numbers that don't get re-estimated.

    `expected_size` is the size the format list promised for this stream, if it did."""
    if d.get("status") == "finished":
        return 1.0
    downloaded = d.get("downloaded_bytes") or 0
    total = d.get("total_bytes")
    if total:
        return min(downloaded / total, 1.0)
    if expected_size:
        return min(downloaded / expected_size, 0.99)
    count = d.get("fragment_count")
    if count:
        # Fragment counts are exact (unlike byte estimates), so the bar can't jump.
        return min(max((d.get("fragment_index") or 1) - 1, 0) / count, 1.0)
    estimate = d.get("total_bytes_estimate")
    if estimate:
        return min(downloaded / estimate, 0.99)
    return 0.0


class ProgressTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.item_index = 0
        self.item_count = 1
        self.item_title: Optional[str] = None
        self.progress = 0.0          # overall, 0..1, never decreases
        self.speed: Optional[float] = None
        self._speed_at = 0.0
        self._eta: Optional[float] = None
        self._eta_at = 0.0
        self._banked_bytes = 0       # bytes of videos that are already finished
        self._streams = []           # per stream of the current item: [downloaded, fraction]
        self._stream_keys = []
        self._weights = []
        self._sizes_known = False
        self._samples = deque()
        self._last_sample = 0.0
        self._t0: Optional[float] = None
        self._all_streams_done = False

    # -- feeding -------------------------------------------------------------

    def start_item(self, index: Optional[int], count: Optional[int], title: Optional[str] = None):
        """Called when a (new) video begins; `index` is 1-based."""
        index = index or 1
        if count:
            self.item_count = max(count, index)
        if title:
            self.item_title = title
        if index == self.item_index:
            return
        self._banked_bytes += sum(s[0] for s in self._streams)
        self._streams, self._stream_keys, self._weights = [], [], []
        self._sizes_known = False
        self._all_streams_done = False
        self.item_index = index
        self._bump(self._overall(0.0))

    def on_hook(self, d: dict):
        info = d.get("info_dict") or {}
        self.start_item(info.get("playlist_index"), info.get("n_entries") or info.get("playlist_count"),
                        info.get("title"))
        if not self._weights:
            self._weights, self._sizes_known = self._expected_weights(info)

        key = d.get("filename") or d.get("tmpfilename") or ""
        if key not in self._stream_keys:
            self._stream_keys.append(key)
            self._streams.append([0, 0.0])
        k = self._stream_keys.index(key)
        stream = self._streams[k]
        expected = self._weights[k] if self._sizes_known and k < len(self._weights) else None
        stream[0] = max(stream[0], d.get("downloaded_bytes") or 0)
        stream[1] = max(stream[1], stream_fraction(d, expected))

        now = self._clock()
        if self._t0 is None:
            self._t0 = now
        if d.get("status") == "downloading":
            raw = d.get("speed")
            if raw:
                self.speed = raw if self.speed is None else (
                    (1 - SPEED_SMOOTHING) * self.speed + SPEED_SMOOTHING * raw)
                self._speed_at = now

        item_fraction = self._item_fraction()
        self._all_streams_done = item_fraction >= 0.999
        self._bump(self._overall(item_fraction))
        self._sample(now)

    def finish(self):
        self.progress = 1.0
        self.speed = None
        self._eta = 0.0
        self._eta_at = self._clock()

    # -- reading -------------------------------------------------------------

    def current_speed(self) -> Optional[float]:
        if self.speed is None or self._all_streams_done or self._clock() - self._speed_at > STALE_AFTER:
            return None
        return self.speed

    def current_eta(self) -> Optional[float]:
        """Seconds left, counting down smoothly between progress updates."""
        if self._eta is None or (self._all_streams_done and self.item_count == 1):
            return None
        return self._eta_now()

    def streams_finished(self) -> bool:
        """True once every stream of the current video is downloaded (merging/converting may follow)."""
        return self._all_streams_done

    def item_size(self) -> Optional[int]:
        """Total bytes of the current video if the site said so up front."""
        return int(sum(self._weights)) if self._sizes_known and self._weights else None

    def bytes_done(self) -> int:
        return self._banked_bytes + sum(s[0] for s in self._streams)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _expected_weights(info: dict):
        """(weight of each stream, whether those weights are real byte sizes)."""
        formats = info.get("requested_formats") or [info]
        sizes = [f.get("filesize") or f.get("filesize_approx") for f in formats]
        if all(sizes):
            return sizes, True
        return [1] * len(formats), False

    def _item_fraction(self) -> float:
        weights = list(self._weights) or [1]
        while len(weights) < len(self._streams):  # more streams than expected
            weights.append(sum(weights) / len(weights))
        done = sum(w * s[1] for w, s in zip(weights, self._streams))
        return min(done / sum(weights), 1.0)

    def _overall(self, item_fraction: float) -> float:
        base = max(self.item_index - 1, 0)
        return (base + DOWNLOAD_SHARE * item_fraction) / max(self.item_count, 1)

    def _bump(self, value: float):
        self.progress = min(max(self.progress, value), 1.0)

    def _sample(self, now: float):
        if now - self._last_sample < SAMPLE_EVERY:
            return
        self._last_sample = now
        self._samples.append((now, self.progress))
        if self.item_count == 1:  # a lone video: only the recent rate matters
            while self._samples and now - self._samples[0][0] > SINGLE_WINDOW:
                self._samples.popleft()
        first_t, first_p = self._samples[0]
        span, gained = now - first_t, self.progress - first_p
        if self.item_count > 1:  # a playlist: average over the whole run, pauses included
            span, gained = now - (now if self._t0 is None else self._t0), self.progress
        if span < MIN_SPAN or gained <= 0:
            return
        raw = (1 - self.progress) * span / gained
        self._eta = raw if self._eta is None else (1 - ETA_SMOOTHING) * self._eta_now() + ETA_SMOOTHING * raw
        self._eta_at = now

    def _eta_now(self) -> float:
        return max((self._eta or 0.0) - (self._clock() - self._eta_at), 0.0)
