from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

ResourceKey = tuple[int, int]


@dataclass(frozen=True)
class HybridRSPacket:
    """Logical SORA hybrid RS update.

    FULL carries the complete state vector. DELTA carries all positions whose
    current state differs from the latest FULL baseline (including explicit
    FREE=0 values). Thus a missed intermediate delta does not break the chain;
    only a missed FULL makes that epoch unusable.
    """
    mode: str
    epoch: int
    seq: int
    states: Mapping[ResourceKey, int]


@dataclass
class HybridRSTransmitter:
    full_interval_s: float = 1.0
    _epoch: int = 0
    _seq: int = -1
    _last_full_s: float = float('-inf')
    _baseline: dict[ResourceKey, int] = field(default_factory=dict)

    def encode(
        self,
        now_s: float,
        full_state: Mapping[ResourceKey, int],
        *,
        max_entries: int | None = None,
    ) -> HybridRSPacket:
        """Encode one full/delta report with an optional on-air entry cap.

        ``full_state`` should already be the sender's deterministic capped
        snapshot when a practical fixed-budget variant is used. Deltas remain
        relative to the latest FULL baseline. If a delta would exceed the
        packet entry budget, an early FULL refresh is sent instead; this avoids
        silently truncating a delta and corrupting receiver reconstruction.
        """
        state = {tuple(k): int(v) for k, v in full_state.items()}
        cap = None if max_entries is None or int(max_entries) <= 0 else int(max_entries)
        if cap is not None and len(state) > cap:
            raise ValueError(
                f"Hybrid full_state has {len(state)} entries but max_entries={cap}; "
                "cap the sender snapshot before encode()."
            )

        due_full = (
            self._seq < 0
            or not self._baseline
            or float(now_s) - self._last_full_s >= float(self.full_interval_s) - 1e-12
        )
        if due_full:
            self._epoch += 1
            self._seq = 0
            self._last_full_s = float(now_s)
            self._baseline = dict(state)
            return HybridRSPacket('full', self._epoch, self._seq, state)

        keys = set(self._baseline) | set(state)
        delta = {
            key: int(state.get(key, 0))
            for key in keys
            if int(state.get(key, 0)) != int(self._baseline.get(key, 0))
        }

        # A bounded practical encoding must never silently drop changed states.
        # If the complete delta does not fit, refresh the bounded FULL baseline.
        if cap is not None and len(delta) > cap:
            self._epoch += 1
            self._seq = 0
            self._last_full_s = float(now_s)
            self._baseline = dict(state)
            return HybridRSPacket('full', self._epoch, self._seq, state)

        self._seq += 1
        return HybridRSPacket('delta', self._epoch, self._seq, delta)


@dataclass
class _RxState:
    valid: bool = False
    epoch: int | None = None
    seq: int = -1
    baseline: dict[ResourceKey, int] = field(default_factory=dict)
    state: dict[ResourceKey, int] = field(default_factory=dict)


class HybridRSReceiver:
    """Per-neighbor hybrid RS reconstruction using full-baseline deltas."""
    def __init__(self):
        self._by_sender: dict[str | int, _RxState] = {}

    def ingest(self, sender_id: str | int, packet: HybridRSPacket) -> tuple[dict[ResourceKey, int] | None, bool]:
        rx = self._by_sender.setdefault(sender_id, _RxState())
        if packet.mode == 'full':
            rx.valid = True
            rx.epoch = int(packet.epoch)
            rx.seq = int(packet.seq)
            rx.baseline = {tuple(k): int(v) for k, v in packet.states.items()}
            rx.state = dict(rx.baseline)
            return dict(rx.state), True

        if packet.mode != 'delta':
            rx.valid = False
            return None, False

        # Any later delta in the same epoch can be decoded from the stored full
        # baseline, even if one or more intermediate deltas were missed.
        valid = (
            rx.valid
            and rx.epoch == int(packet.epoch)
            and int(packet.seq) > rx.seq
        )
        if not valid:
            return None, False

        rebuilt = dict(rx.baseline)
        for key, value in packet.states.items():
            rebuilt[tuple(key)] = int(value)
        rx.state = rebuilt
        rx.seq = int(packet.seq)
        return dict(rx.state), True

    def invalidate(self, sender_id: str | int) -> None:
        rx = self._by_sender.setdefault(sender_id, _RxState())
        rx.valid = False

    def state_for(self, sender_id: str | int) -> dict[ResourceKey, int] | None:
        rx = self._by_sender.get(sender_id)
        if rx is None or not rx.valid:
            return None
        return dict(rx.state)
