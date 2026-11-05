package main

import (
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

// clock is a settable time for the stale bound.
type clock struct{ t time.Time }

func (c *clock) now() time.Time { return c.t }

func epoch(seconds float64) time.Time {
	return time.Unix(0, int64(seconds*1e9))
}

// familiesOf gathers g and returns, per metric family, how many series carry
// sender's label.
func familiesOf(t *testing.T, g prometheus.Gatherer, sender string) map[string]int {
	t.Helper()
	families, err := g.Gather()
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]int{}
	for _, f := range families {
		for _, metric := range f.GetMetric() {
			for _, l := range metric.GetLabel() {
				if l.GetName() == "sender" && l.GetValue() == sender {
					out[f.GetName()]++
				}
			}
		}
	}
	return out
}

const fullAt = 1790949297.656 // `full`'s received_at

const s0 = "0a0b0c0d" // the fixtures' sender

// healthAt and detailAt are the fixtures, received at `at`.
func healthAt(at float64) []byte {
	return []byte(strings.Replace(full, `"received_at": 1790949297.656`, fmt.Sprintf(`"received_at": %.3f`, at), 1))
}

func detailAt(at float64) []byte {
	return []byte(strings.Replace(detailFull, `"received_at": 1000`, fmt.Sprintf(`"received_at": %.3f`, at), 1))
}

// board is a registry with both kinds of metrics, sharing one Freshness on a
// settable clock, and a scrape that prunes as the backend's does.
type board struct {
	reg    *prometheus.Registry
	health *Metrics
	detail *DetailMetrics
	clock  *clock
	scrape prometheus.Gatherer
}

func newBoard(now float64) *board {
	b := &board{reg: prometheus.NewRegistry(), clock: &clock{epoch(now)}}
	b.health = NewMetrics(b.reg)
	b.detail = NewDetailMetrics(b.reg)
	fresh := NewFreshness(DefaultStaleAfter, b.clock.now)
	b.health.Fresh = fresh
	b.detail.Fresh = fresh
	b.scrape = pruningGatherer(b.reg, b.health.Prune, b.detail.Prune)
	return b
}

func (b *board) apply(t *testing.T, apply func([]byte) error, payload []byte) {
	t.Helper()
	if err := apply(payload); err != nil {
		t.Fatal(err)
	}
}

const minute = 60.0

func TestASilentBoardsHealthSeriesAreDroppedAtScrapeTime(t *testing.T) {
	b := newBoard(fullAt + 1)
	b.apply(t, b.health.Apply, []byte(full))

	// Inside the bound: live.
	b.clock.t = epoch(fullAt + DefaultStaleAfter.Seconds() - 1)
	if got := familiesOf(t, b.scrape, s0); got["mesh_board_uptime_seconds"] != 1 || got["mesh_board_interface_up"] != 5 {
		t.Fatalf("a board inside the bound lost its series: %v", got)
	}

	// Past it: only the received timestamp and the name are left.
	b.clock.t = epoch(fullAt + DefaultStaleAfter.Seconds() + 1)
	got := familiesOf(t, b.scrape, s0)
	if len(got) != 2 || got["mesh_board_report_received_timestamp_seconds"] != 1 || got["mesh_board_info"] != 1 {
		t.Fatalf("stale board's series: %v, want only its received timestamp and name", got)
	}
	if v := testutil.ToFloat64(b.health.received.WithLabelValues(s0)); v != fullAt {
		t.Errorf("received timestamp %v, want %v", v, fullAt)
	}

	// A fresh report brings it back.
	b.clock.t = epoch(fullAt + 3600)
	b.apply(t, b.health.Apply, healthAt(fullAt+3599))
	if got := familiesOf(t, b.scrape, s0); got["mesh_board_uptime_seconds"] != 1 {
		t.Errorf("a fresh report did not restore the series: %v", got)
	}
}

func TestARetainedOldReportDoesNotResurrectTheBoard(t *testing.T) {
	// The backend reconnects twelve days after the board's last report, and
	// the broker replays that report as it would a live one.
	b := newBoard(fullAt + 12*24*3600)
	b.apply(t, b.health.Apply, []byte(full))
	// Judged before any scrape: Apply itself must not set the health series.
	if n := testutil.CollectAndCount(b.health.uptime); n != 0 {
		t.Errorf("uptime set from a twelve-day-old report: %d series", n)
	}
	if n := testutil.CollectAndCount(b.health.up); n != 0 {
		t.Errorf("interface_up set from a twelve-day-old report: %d series", n)
	}
	if v := testutil.ToFloat64(b.health.received.WithLabelValues(s0)); v != fullAt {
		t.Errorf("received timestamp %v, want %v: Last report and Silent need it", v, fullAt)
	}
	if n := testutil.CollectAndCount(b.health.info); n != 1 {
		t.Errorf("%d info series, want 1: the Silent panel names boards through it", n)
	}
}

func TestAZeroBoundKeepsEverything(t *testing.T) {
	m := NewMetrics(prometheus.NewRegistry())
	m.Fresh = NewFreshness(0, (&clock{epoch(fullAt + 12*24*3600)}).now)
	if err := m.Apply([]byte(full)); err != nil {
		t.Fatal(err)
	}
	m.Prune()
	if n := testutil.CollectAndCount(m.uptime); n != 1 {
		t.Errorf("a zero bound dropped the series: %d", n)
	}
}

// Detail comes every 30 min, health every 5: a live board's detail must stay
// up between its detail reports (review, #20).
func TestALiveBoardKeepsItsDetailBetweenDetailReports(t *testing.T) {
	const t0 = fullAt
	b := newBoard(t0)
	b.apply(t, b.detail.Apply, detailAt(t0))
	for at := t0; at <= t0+30*minute; at += 5 * minute {
		b.clock.t = epoch(at)
		b.apply(t, b.health.Apply, healthAt(at))
	}
	// Health 5 min old, detail 30 min old.
	b.clock.t = epoch(t0 + 35*minute)
	got := familiesOf(t, b.scrape, s0)
	if got["mesh_board_firmware_info"] != 1 || got["mesh_link_heard_timestamp_seconds"] != 2 || got["mesh_board_uptime_seconds"] != 1 {
		t.Fatalf("a live board lost series between detail reports: %v", got)
	}
}

func TestASilentBoardsDetailSeriesAreDroppedAtScrapeTime(t *testing.T) {
	const t0 = fullAt
	b := newBoard(t0)
	b.apply(t, b.detail.Apply, detailAt(t0))
	b.clock.t = epoch(t0 + 5*minute)
	b.apply(t, b.health.Apply, healthAt(t0+5*minute))
	// Silent past the bound since its last report of either kind.
	b.clock.t = epoch(t0 + 5*minute + DefaultStaleAfter.Seconds() + 1)
	got := familiesOf(t, b.scrape, s0)
	want := []string{"mesh_board_report_received_timestamp_seconds", "mesh_board_detail_received_timestamp_seconds", "mesh_board_info"}
	if len(got) != len(want) {
		t.Fatalf("silent board's series: %v, want only %v", got, want)
	}
	for _, name := range want {
		if got[name] != 1 {
			t.Fatalf("silent board's series: %v, want only %v", got, want)
		}
	}
	// The mesh's node names are not the board's to lose.
	if n := testutil.CollectAndCount(b.detail.nodeInfo); n != 3 {
		t.Errorf("%d node names, want 3", n)
	}
	// A fresh detail report replaces what was dropped, without a duplicate.
	b.clock.t = epoch(t0 + 3600)
	b.apply(t, b.detail.Apply, detailAt(t0+3599))
	if got := familiesOf(t, b.scrape, s0); got["mesh_board_firmware_info"] != 1 || got["mesh_board_interface_online"] != 2 {
		t.Errorf("a fresh detail report did not restore the series: %v", got)
	}
}

func TestARetainedOldDetailReportDoesNotResurrectTheBoard(t *testing.T) {
	b := newBoard(1000 + 12*24*3600)
	b.apply(t, b.detail.Apply, []byte(detailFull))
	b.apply(t, b.health.Apply, healthAt(1000))
	if _, err := b.scrape.Gather(); err != nil {
		t.Fatal(err)
	}
	for name, g := range map[string]*prometheus.GaugeVec{"firmware": b.detail.firmware, "rssi": b.detail.rssi,
		"interface rx": b.detail.ifRx, "link heard": b.detail.linkHeard, "pn messages": b.detail.pnMessages} {
		if n := testutil.CollectAndCount(g); n != 0 {
			t.Errorf("%s set from a twelve-day-old detail report: %d series", name, n)
		}
	}
	if v := testutil.ToFloat64(b.detail.received.WithLabelValues(s0)); v != 1000 {
		t.Errorf("detail received timestamp %v, want 1000", v)
	}
}

// On reconnect the broker replays both retained reports in no set order. A
// 25-minute-old detail replayed before a 3-minute-old health report belongs
// to a live board, and must be up by the next scrape.
func TestARetainedDetailReplayedBeforeItsHealthIsKept(t *testing.T) {
	const now = fullAt + 3600
	b := newBoard(now)
	b.apply(t, b.detail.Apply, detailAt(now-25*minute))
	b.apply(t, b.health.Apply, healthAt(now-3*minute))
	if got := familiesOf(t, b.scrape, s0); got["mesh_board_firmware_info"] != 1 || got["mesh_link_heard_timestamp_seconds"] != 2 {
		t.Errorf("a live board's retained detail was lost to replay order: %v", got)
	}
}

// A report older than one already applied -- a bridge replaying its queue
// late -- neither lowers the board's latest time nor drops a live board.
func TestALateOlderReportChangesNothing(t *testing.T) {
	const now = fullAt + 3600
	b := newBoard(now)
	b.apply(t, b.health.Apply, healthAt(now-minute))
	b.apply(t, b.detail.Apply, detailAt(now-minute))
	late := strings.Replace(string(healthAt(now-40*minute)), `"uptime_s": 3600`, `"uptime_s": 1`, 1)
	b.apply(t, b.health.Apply, []byte(late))
	b.apply(t, b.detail.Apply, detailAt(now-40*minute))
	got := familiesOf(t, b.scrape, s0)
	if got["mesh_board_uptime_seconds"] != 1 || got["mesh_board_firmware_info"] != 1 {
		t.Fatalf("a late, older report dropped a live board: %v", got)
	}
	if v := testutil.ToFloat64(b.health.uptime.WithLabelValues(s0)); v != 3600 {
		t.Errorf("uptime %v from the older report, want 3600", v)
	}
	if v := testutil.ToFloat64(b.health.received.WithLabelValues(s0)); v != now-minute {
		t.Errorf("health received timestamp lowered to %v", v)
	}
	if v := testutil.ToFloat64(b.detail.received.WithLabelValues(s0)); v != now-minute {
		t.Errorf("detail received timestamp lowered to %v", v)
	}
}

func TestTheStaleBoundComesFromTheEnvironment(t *testing.T) {
	t.Setenv("TELEMETRY_STALE_AFTER", "")
	if d, err := staleAfterDefault(); err != nil || d != 15*time.Minute {
		t.Errorf("unset: %v, %v; want 15m", d, err)
	}
	t.Setenv("TELEMETRY_STALE_AFTER", "30m")
	if d, err := staleAfterDefault(); err != nil || d != 30*time.Minute {
		t.Errorf("30m: %v, %v", d, err)
	}
	for _, bad := range []string{"15", "soon", "-1m"} {
		t.Setenv("TELEMETRY_STALE_AFTER", bad)
		if _, err := staleAfterDefault(); err == nil {
			t.Errorf("accepted TELEMETRY_STALE_AFTER=%q", bad)
		}
	}
}
