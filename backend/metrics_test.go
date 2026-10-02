package main

import (
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

const full = `{"v": 1, "sender": "0a0b0c0d", "received_at": 1790949297.656, "hops": 2, "via": "x", "gateway": "gw-1",
 "uptime_s": 3600, "reset": "panic", "boots": 1, "crashes": 9, "panics": 8, "heap_bytes": 25600,
 "largest_bytes": 18432, "psram_bytes": 2048000, "interfaces_present": ["lora", "ble"], "interfaces_up": ["lora"],
 "ble_peers": 1, "espnow_peers": 0, "paths": 23, "nodes": 5, "relaying": true, "relay_expected": false,
 "time_current": true, "battery_mv": 3900, "battery_pct": 80}`

func TestAReportSetsItsBoardsSeries(t *testing.T) {
	m := NewMetrics(prometheus.NewRegistry())
	if err := m.Apply([]byte(full)); err != nil {
		t.Fatal(err)
	}
	s := "0a0b0c0d"
	checks := []struct {
		name      string
		got, want float64
	}{
		{"heap", testutil.ToFloat64(m.heap.WithLabelValues(s)), 25600},
		{"crashes", testutil.ToFloat64(m.crashes.WithLabelValues(s)), 9},
		{"lora up", testutil.ToFloat64(m.up.WithLabelValues(s, "lora")), 1},
		{"ble up", testutil.ToFloat64(m.up.WithLabelValues(s, "ble")), 0},
		{"ble present", testutil.ToFloat64(m.present.WithLabelValues(s, "ble")), 1},
		{"wifi present", testutil.ToFloat64(m.present.WithLabelValues(s, "wifi")), 0},
		{"reset panic", testutil.ToFloat64(m.reset.WithLabelValues(s, "panic")), 1},
		{"reset poweron", testutil.ToFloat64(m.reset.WithLabelValues(s, "poweron")), 0},
		{"battery mV", testutil.ToFloat64(m.batteryMV.WithLabelValues(s)), 3900},
		{"relaying", testutil.ToFloat64(m.relaying.WithLabelValues(s)), 1},
		{"relay expected", testutil.ToFloat64(m.relayExpected.WithLabelValues(s)), 0},
		{"hops", testutil.ToFloat64(m.hops.WithLabelValues(s)), 2},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, c.got, c.want)
		}
	}
}

func TestAnAbsentBatteryRemovesTheSeriesRatherThanZeroingIt(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewMetrics(reg)
	_ = m.Apply([]byte(full))
	noBattery := strings.Replace(strings.Replace(full, `"battery_mv": 3900`, `"battery_mv": null`, 1),
		`"battery_pct": 80`, `"battery_pct": null`, 1)
	if err := m.Apply([]byte(noBattery)); err != nil {
		t.Fatal(err)
	}
	if n := testutil.CollectAndCount(m.batteryMV); n != 0 {
		t.Errorf("battery series still present: %d", n)
	}
}

func TestABadMessageChangesNothing(t *testing.T) {
	m := NewMetrics(prometheus.NewRegistry())
	_ = m.Apply([]byte(full))
	for _, bad := range []string{`{not json`, `{"v": 2, "sender": "0a0b0c0d"}`, `{"v": 1, "sender": "short"}`} {
		if err := m.Apply([]byte(bad)); err == nil {
			t.Errorf("accepted %q", bad)
		}
	}
	if got := testutil.ToFloat64(m.heap.WithLabelValues("0a0b0c0d")); got != 25600 {
		t.Errorf("a refused message changed the heap series: %v", got)
	}
	if got := testutil.ToFloat64(m.refused); got != 3 {
		t.Errorf("refused = %v, want 3", got)
	}
}
