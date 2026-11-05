"""When the gateway announces: at start, every interval, and when one of its
host's Reticulum interfaces comes up.

A board finds a gateway only by hearing its announce. A board or phone that
has just joined a network -- a RAD's access point, a LAN the gateway's host
just reached -- would otherwise wait up to a whole announce interval (ten
minutes by default). So an interface that comes up, or comes back after being
down, earns one more announce, at most one per MIN_GAP_S however much the
interfaces flap.

Interfaces are read from the gateway's Reticulum instance: its own, and, for a
client of a shared instance (rnsd), the shared instance's through its RPC --
the same list `rnstatus` shows. While the shared instance cannot be reached
its interfaces read as absent, so when it is back they count as come up.

Pure apart from interface_states(), which takes the objects it reads, so the
tests pass stand-ins; telemetry_gateway.py announces.
"""

MIN_GAP_S = 10.0   # at most one announce per this many seconds for interfaces coming up
POLL_S = 2.0       # how often the interfaces are looked at


def interface_states(reticulum, transport):
    """{interface name: online} for every interface the gateway's host has.

    `transport.interfaces` are this process's own (for a client of a shared
    instance, its link to it); `reticulum.get_interface_stats()` adds the
    shared instance's. A failed read of the latter leaves them out."""
    states = {}
    for interface in list(getattr(transport, "interfaces", None) or []):
        states[str(interface)] = bool(getattr(interface, "online", False))
    try:
        stats = reticulum.get_interface_stats() or {}
    except Exception:                                   # noqa: BLE001 -- rnsd away, RPC refused
        stats = {}
    for entry in stats.get("interfaces", None) or []:
        name = entry.get("name")
        if name is not None:
            states[name] = states.get(name, False) or bool(entry.get("status"))
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

    def announced(self, now):
        """Record an announce, whatever its reason: the next periodic one is an
        interval after it, and the gap for interfaces coming up runs from it."""
        self._last = now
        self._next = now + self.interval_s

    def due(self, now, states):
        """What an announce now would be for: "start", "interval", or the list
        of interfaces that came up; None when no announce is due.

        The first look only learns what is up: the start-up announce covers
        it. After that, an interface online now that was offline or absent at
        the last look has come up."""
        if self._states is not None:
            for name, up in states.items():
                if up and not self._states.get(name, False) and name not in self._came_up:
                    self._came_up.append(name)
        self._states = dict(states)
        # One that went down again before its announce is not announced for.
        self._came_up = [name for name in self._came_up if states.get(name, False)]
        if self._last is None:
            self._came_up = []
            return "start"
        if self._came_up and now - self._last >= self.min_gap_s:
            came_up, self._came_up = self._came_up, []
            return came_up
        if now >= self._next:
            self._came_up = []   # this announce serves them too
            return "interval"
        return None
