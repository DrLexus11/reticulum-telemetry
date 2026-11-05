"""When the gateway announces: at start, every interval, and when one of its
host's Reticulum interfaces comes up.

A board finds a gateway only by hearing its announce. A board or phone that
has just joined a network -- a RAD's access point, a LAN the gateway's host
just reached -- would otherwise wait up to a whole announce interval (ten
minutes by default). So an interface that comes up, or comes back after being
down, earns one more announce.

Every such announce is rebroadcast onto LoRa by the boards, so they are
rationed: the first waits at least MIN_GAP_S after the previous announce, and
each one after it doubles the gap, up to the regular interval. A quiet interval
(no interface coming up) resets the gap. Interfaces a server spawns per client
connection (a TCP server's clients, AutoInterface peers) are not counted: their
names change on every reconnect, so a client stuck reconnecting would look
like a new interface on every poll.

Interfaces are read from the gateway's Reticulum instance: its own, and, for a
client of a shared instance (rnsd), the shared instance's through its RPC --
the same list `rnstatus` shows. That RPC has no timeout and rnsd answers one
client at a time, so it is read on a thread of its own (InterfaceReader): a
stalled read leaves the snapshot stale, the schedule treats a stale snapshot as
no change, and the start-up and periodic announces never wait on it. While the
shared instance cannot be reached its interfaces read as absent, so when it is
back they count as come up.

Pure apart from interface_states() and InterfaceReader, which take the objects
they read, so the tests pass stand-ins; telemetry_gateway.py announces.
"""

import threading
import time

MIN_GAP_S = 10.0   # the shortest gap before an announce for an interface coming up
POLL_S = 2.0       # how often the interfaces are looked at
STALE_S = 10.0     # a snapshot older than this is no evidence of change


def _spawned(entry_parent):
    return entry_parent is not None and entry_parent != ""


def interface_states(reticulum, transport):
    """{interface name: online} for every interface the gateway's host has,
    except those a server spawned per client connection.

    `transport.interfaces` are this process's own (for a client of a shared
    instance, its link to it); `reticulum.get_interface_stats()` adds the
    shared instance's. A failed read of the latter leaves them out."""
    states = {}
    for interface in list(getattr(transport, "interfaces", None) or []):
        if _spawned(getattr(interface, "parent_interface", None)):
            continue
        states[str(interface)] = bool(getattr(interface, "online", False))
    try:
        stats = reticulum.get_interface_stats() or {}
    except Exception:                                   # noqa: BLE001 -- rnsd away, RPC refused
        stats = {}
    for entry in stats.get("interfaces", None) or []:
        name = entry.get("name")
        if name is None or _spawned(entry.get("parent_interface_name")):
            continue
        states[name] = states.get(name, False) or bool(entry.get("status"))
    return states


class InterfaceReader:
    """Reads interface_states() every POLL_S on a daemon thread and keeps the
    latest as (monotonic time read, states). A read that hangs leaves the
    snapshot to age; it never holds up the caller."""

    def __init__(self, read, poll_s=POLL_S, clock=time.monotonic):
        self._read = read
        self._poll_s = poll_s
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot = (None, None)

    def read_once(self):
        states = self._read()
        with self._lock:
            self._snapshot = (self._clock(), states)

    def latest(self):
        with self._lock:
            return self._snapshot

    def start(self):
        threading.Thread(target=self._forever, daemon=True, name="interface-states").start()
        return self

    def _forever(self):
        while True:
            try:
                self.read_once()
            except Exception:                           # noqa: BLE001 -- the next read tries again
                pass
            time.sleep(self._poll_s)


def fresh(snapshot, now, stale_s=STALE_S):
    """The snapshot's states if read within stale_s of now, else None."""
    at, states = snapshot
    if at is None or now - at > stale_s:
        return None
    return states


class AnnounceSchedule:
    """Says when to announce. `now` is a monotonic clock, in seconds."""

    def __init__(self, interval_s, min_gap_s=MIN_GAP_S):
        self.interval_s = interval_s
        self.min_gap_s = min_gap_s
        self._states = None      # as last seen; None before the first look
        self._came_up = []       # interfaces that came up and are not yet announced for
        self._last = None        # the last announce, any reason
        self._next = None        # the next periodic announce
        self._gap = min_gap_s    # the gap the next interface-up announce waits
        self._last_rise = None   # when an interface was last seen coming up

    @property
    def gap_s(self):
        return self._gap

    def announced(self, now):
        """Record an announce, whatever its reason: the next periodic one is an
        interval after it, and the gap for interfaces coming up runs from it."""
        self._last = now
        self._next = now + self.interval_s

    def due(self, now, states):
        """What an announce now would be for: "start", "interval", or the list
        of interfaces that came up; None when no announce is due.

        `states` None means no fresh look at the interfaces: nothing has come
        up, and the start and periodic announces go ahead regardless. The first
        look only learns what is up: the start-up announce covers it. After
        that, an interface online now that was offline or absent at the last
        look has come up."""
        if states is not None:
            if self._states is not None:
                for name, up in states.items():
                    if up and not self._states.get(name, False):
                        self._rise(now)
                        if name not in self._came_up:
                            self._came_up.append(name)
            self._states = dict(states)
            # One that went down again before its announce is not announced for.
            self._came_up = [name for name in self._came_up if states.get(name, False)]
        if self._last is None:
            self._came_up = []
            return "start"
        if self._came_up and now - self._last >= self._gap:
            came_up, self._came_up = self._came_up, []
            self._gap = min(self._gap * 2, max(self.interval_s, self.min_gap_s))
            return came_up
        if now >= self._next:
            self._came_up = []   # this announce serves them too
            return "interval"
        return None

    def _rise(self, now):
        # A quiet interval since the last interface came up starts the gap over.
        if self._last_rise is not None and now - self._last_rise >= self.interval_s:
            self._gap = self.min_gap_s
        self._last_rise = now
