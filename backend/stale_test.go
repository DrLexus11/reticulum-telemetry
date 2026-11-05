package main

import (
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

func TestASilentBoardsHealthSeriesAreDroppedAtScrapeTime(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewMetrics(reg)
	c := &clock{epoch(fullAt + 1)}
	m.Stale = Staleness{After: DefaultStaleAfter, Now: c.now}
	scrape := pruningGatherer(reg, m.Prune)
	if err := m.Apply([]byte(full)); err != nil {
		t.Fatal(err)
	}
	s := "0a0b0c0d"

	// Inside the bound: live.
	c.t = epoch(fullAt + DefaultStaleAfter.Seconds() - 1)
	if got := familiesOf(t, scrape, s); got["mesh_board_uptime_seconds"] != 1 || got["mesh_board_interface_up"] != 5 {
		t.Fatalf("a board inside the bound lost its series: %v", got)
	}

	// Past it: only the received timestamp and the name are left.
	c.t = epoch(fullAt + DefaultStaleAfter.Seconds() + 1)
	got := familiesOf(t, scrape, s)
	want := map[string]int{"mesh_board_report_received_timestamp_seconds": 1, "mesh_board_info": 1}
	if len(got) != len(want) || got["mesh_board_report_received_timestamp_seconds"] != 1 || got["mesh_board_info"] != 1 {
		t.Fatalf("stale board's series: %v, want only %v", got, want)
	}
	if v := testutil.ToFloat64(m.received.WithLabelValues(s)); v != fullAt {
		t.Errorf("received timestamp %v, want %v", v, fullAt)
	}

	// A fresh report brings it back.
	fresh := strings.Replace(full, `"received_at": 1790949297.656`, `"received_at": 1790951000`, 1)
	c.t = epoch(1790951001)
	if err := m.Apply([]byte(fresh)); err != nil {
		t.Fatal(err)
	}
	if got := familiesOf(t, scrape, s); got["mesh_board_uptime_seconds"] != 1 {
		t.Errorf("a fresh report did not restore the series: %v", got)
	}
}

func TestARetainedOldReportDoesNotResurrectTheBoard(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewMetrics(reg)
	// The backend reconnects twelve days after the board's last report, and
	// the broker replays that report as it would a live one.
	m.Stale = Staleness{After: DefaultStaleAfter, Now: (&clock{epoch(fullAt + 12*24*3600)}).now}
	if err := m.Apply([]byte(full)); err != nil {
		t.Fatal(err)
	}
	s := "0a0b0c0d"
	// Judged before any scrape: Apply itself must not set the health series.
	if n := testutil.CollectAndCount(m.uptime); n != 0 {
		t.Errorf("uptime set from a twelve-day-old report: %d series", n)
	}
	if n := testutil.CollectAndCount(m.up); n != 0 {
		t.Errorf("interface_up set from a twelve-day-old report: %d series", n)
	}
	if v := testutil.ToFloat64(m.received.WithLabelValues(s)); v != fullAt {
		t.Errorf("received timestamp %v, want %v: Last report and Silent need it", v, fullAt)
	}
	if n := testutil.CollectAndCount(m.info); n != 1 {
		t.Errorf("%d info series, want 1: the Silent panel names boards through it", n)
	}
}

func TestAZeroBoundKeepsEverything(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewMetrics(reg)
	m.Stale = Staleness{Now: (&clock{epoch(fullAt + 12*24*3600)}).now}
	if err := m.Apply([]byte(full)); err != nil {
		t.Fatal(err)
	}
	m.Prune()
	if n := testutil.CollectAndCount(m.uptime); n != 1 {
		t.Errorf("a zero bound dropped the series: %d", n)
	}
}

func TestASilentBoardsDetailSeriesAreDroppedAtScrapeTime(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewDetailMetrics(reg)
	c := &clock{epoch(1001)}
	m.Stale = Staleness{After: DefaultStaleAfter, Now: c.now}
	scrape := pruningGatherer(reg, m.Prune)
	if err := m.Apply([]byte(detailFull)); err != nil {
		t.Fatal(err)
	}
	s := "0a0b0c0d"
	if got := familiesOf(t, scrape, s); got["mesh_board_firmware_info"] != 1 || got["mesh_link_heard_timestamp_seconds"] != 2 {
		t.Fatalf("a fresh detail report's series are missing: %v", got)
	}
	c.t = epoch(1000 + DefaultStaleAfter.Seconds() + 1)
	got := familiesOf(t, scrape, s)
	if len(got) != 1 || got["mesh_board_detail_received_timestamp_seconds"] != 1 {
		t.Fatalf("stale board's detail series: %v, want only its received timestamp", got)
	}
	// The mesh's node names are not the board's to lose.
	if n := testutil.CollectAndCount(m.nodeInfo); n != 3 {
		t.Errorf("%d node names, want 3", n)
	}
	// A fresh one replaces what was dropped, without a duplicate series.
	fresh := strings.Replace(detailFull, `"received_at": 1000`, `"received_at": 5000`, 1)
	c.t = epoch(5001)
	if err := m.Apply([]byte(fresh)); err != nil {
		t.Fatal(err)
	}
	if got := familiesOf(t, scrape, s); got["mesh_board_firmware_info"] != 1 || got["mesh_board_interface_online"] != 2 {
		t.Errorf("a fresh detail report did not restore the series: %v", got)
	}
}

func TestARetainedOldDetailReportDoesNotResurrectTheBoard(t *testing.T) {
	m := NewDetailMetrics(prometheus.NewRegistry())
	m.Stale = Staleness{After: DefaultStaleAfter, Now: (&clock{epoch(1000 + 12*24*3600)}).now}
	if err := m.Apply([]byte(detailFull)); err != nil {
		t.Fatal(err)
	}
	for name, g := range map[string]*prometheus.GaugeVec{"firmware": m.firmware, "rssi": m.rssi,
		"interface rx": m.ifRx, "link heard": m.linkHeard, "pn messages": m.pnMessages} {
		if n := testutil.CollectAndCount(g); n != 0 {
			t.Errorf("%s set from a twelve-day-old detail report: %d series", name, n)
		}
	}
	if v := testutil.ToFloat64(m.received.WithLabelValues("0a0b0c0d")); v != 1000 {
		t.Errorf("detail received timestamp %v, want 1000", v)
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
