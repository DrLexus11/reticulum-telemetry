package main

import (
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

const detailFull = `{"v": 1, "sender": "0a0b0c0d", "name": "RAD-1", "received_at": 1000, "hops": 1, "via": "x",
 "gateway": "gw", "uptime_s": 3600,
 "firmware": {"hash": "a1b2c3d4", "version": "1.86", "env": "impr-rad01-rev1"},
 "interfaces": [{"interface": "lora", "up": true, "rx_bytes": 100, "tx_bytes": 50},
                {"interface": "tcp_client", "up": true, "rx_bytes": 10, "tx_bytes": 5},
                {"interface": "tcp_client", "up": false, "rx_bytes": 1, "tx_bytes": 2}],
 "radio": {"rssi_dbm": -97, "snr_db": 6.25, "noise_dbm": null, "utilisation_pct": 12, "airtime_pct": 3},
 "propagation": {"messages": 4, "bytes": 3072, "peers": 1, "sync_ok": 9, "sync_failed": 2, "last_sync_s": null},
 "neighbours": [{"node": "11223344", "name": "Ayse", "interface": "lora", "rssi_dbm": -90, "heard_s": 60},
                {"node": "55667788", "name": null, "interface": "tcp_server", "rssi_dbm": null, "heard_s": 0}],
 "neighbours_truncated": false}`

func TestADetailReportSetsItsSeries(t *testing.T) {
	m := NewDetailMetrics(prometheus.NewRegistry())
	if err := m.Apply([]byte(detailFull)); err != nil {
		t.Fatal(err)
	}
	s := "0a0b0c0d"
	checks := []struct {
		name      string
		got, want float64
	}{
		{"firmware", testutil.ToFloat64(m.firmware.WithLabelValues(s, "a1b2c3d4", "1.86", "impr-rad01-rev1")), 1},
		{"lora rx", testutil.ToFloat64(m.ifRx.WithLabelValues(s, "lora")), 100},
		{"tcp clients summed", testutil.ToFloat64(m.ifTx.WithLabelValues(s, "tcp_client")), 7},
		{"tcp up if any is", testutil.ToFloat64(m.ifUp.WithLabelValues(s, "tcp_client")), 1},
		{"rssi", testutil.ToFloat64(m.rssi.WithLabelValues(s)), -97},
		{"snr", testutil.ToFloat64(m.snr.WithLabelValues(s)), 6.25},
		{"pn messages", testutil.ToFloat64(m.pnMessages.WithLabelValues(s)), 4},
		{"pn failed", testutil.ToFloat64(m.pnFail.WithLabelValues(s)), 2},
		{"heard at", testutil.ToFloat64(m.linkHeard.WithLabelValues(s, "11223344", "lora", "RAD-1", "Ayse")), 940},
		{"link rssi", testutil.ToFloat64(m.linkRSSI.WithLabelValues(s, "11223344", "lora", "RAD-1", "Ayse")), -90},
		{"neighbour named", testutil.ToFloat64(m.nodeInfo.WithLabelValues("11223344", "Ayse")), 1},
		{"board named", testutil.ToFloat64(m.nodeInfo.WithLabelValues(s, "RAD-1")), 1},
		{"unnamed neighbour by its id", testutil.ToFloat64(m.nodeInfo.WithLabelValues("55667788", "55667788")), 1},
		{"unnamed link end", testutil.ToFloat64(m.linkHeard.WithLabelValues(s, "55667788", "tcp_server", "RAD-1", "55667788")), 1000},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, c.got, c.want)
		}
	}
	// Unknowns are absent, not zero.
	if n := testutil.CollectAndCount(m.noise); n != 0 {
		t.Errorf("noise: %d series, want none", n)
	}
	if n := testutil.CollectAndCount(m.pnAge); n != 0 {
		t.Errorf("last sync age: %d series, want none", n)
	}
	if n := testutil.CollectAndCount(m.linkRSSI); n != 1 {
		t.Errorf("link rssi: %d series, want only the measured one", n)
	}
}

func TestANeighbourThatIsGoneLosesItsSeries(t *testing.T) {
	m := NewDetailMetrics(prometheus.NewRegistry())
	_ = m.Apply([]byte(detailFull))
	later := strings.Replace(detailFull, `{"node": "11223344", "name": "Ayse", "interface": "lora", "rssi_dbm": -90, "heard_s": 60},`, "", 1)
	later = strings.Replace(later, `"hash": "a1b2c3d4"`, `"hash": "deadbeef"`, 1)
	later = strings.Replace(later, `"radio": {"rssi_dbm": -97, "snr_db": 6.25, "noise_dbm": null, "utilisation_pct": 12, "airtime_pct": 3}`, `"radio": null`, 1)
	if err := m.Apply([]byte(later)); err != nil {
		t.Fatal(err)
	}
	if n := testutil.CollectAndCount(m.linkHeard); n != 1 {
		t.Errorf("links: %d series, want 1", n)
	}
	if n := testutil.CollectAndCount(m.firmware); n != 1 {
		t.Errorf("firmware: %d series, want the new image only", n)
	}
	if n := testutil.CollectAndCount(m.rssi); n != 0 {
		t.Errorf("rssi: %d series after the radio went, want none", n)
	}
}

func TestAMalformedDetailIsRefusedAndChangesNothing(t *testing.T) {
	m := NewDetailMetrics(prometheus.NewRegistry())
	_ = m.Apply([]byte(detailFull))
	for _, bad := range []string{
		`{"v": 1}`,
		strings.Replace(detailFull, `"v": 1`, `"v": 2`, 1),
		strings.Replace(detailFull, `"node": "11223344"`, `"node": "Ayse"`, 1),
		strings.Replace(detailFull, `"heard_s": 60`, `"heard_s": -1`, 1),
		"not json",
		strings.Replace(detailFull, `{"interface": "lora", "up": true, "rx_bytes": 100, "tx_bytes": 50}`, `{}`, 1),
		strings.Replace(detailFull, `"radio": {`, `"radio_": {`, 1),
		strings.Replace(detailFull, `"neighbours_truncated": false`, `"truncated": false`, 1),
		strings.Replace(detailFull, `"messages": 4, `, ``, 1),
		strings.Replace(detailFull, `"utilisation_pct": 12`, `"utilisation_pct": 140`, 1),
		strings.Replace(detailFull, `"rssi_dbm": -90, `, ``, 1),
		strings.Replace(detailFull, `"hash": "a1b2c3d4"`, `"hash": "zz"`, 1),
	} {
		if err := m.Apply([]byte(bad)); err == nil {
			t.Errorf("accepted %.40q", bad)
		}
	}
	if got := testutil.ToFloat64(m.refused); got != 12 {
		t.Errorf("refused: %v, want 12", got)
	}
	if got := testutil.ToFloat64(m.ifRx.WithLabelValues("0a0b0c0d", "lora")); got != 100 {
		t.Errorf("lora rx changed to %v by a refused message", got)
	}
}
