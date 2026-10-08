package main

import (
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

const probeOK = `{"v": 1, "sender": "0a0b0c0d", "name": "RAD-1", "at": 1000, "delivered": true, "rtt_s": 2.5,
 "hops": 2, "via": "lora", "gateway": "gw", "sent_total": 10, "delivered_total": 9}`

func TestAProbeSetsReachabilityAndRTT(t *testing.T) {
	m := NewProbeMetrics(prometheus.NewRegistry())
	if err := m.Apply([]byte(probeOK)); err != nil {
		t.Fatal(err)
	}
	s := "0a0b0c0d"
	for _, c := range []struct {
		name      string
		got, want float64
	}{
		{"success", testutil.ToFloat64(m.success.WithLabelValues(s)), 1},
		{"rtt", testutil.ToFloat64(m.rtt.WithLabelValues(s, "lora")), 2.5},
		{"hops", testutil.ToFloat64(m.hops.WithLabelValues(s)), 2},
		{"sent", testutil.ToFloat64(m.sent.WithLabelValues(s)), 10},
		{"delivered", testutil.ToFloat64(m.delivered.WithLabelValues(s)), 9},
	} {
		if c.got != c.want {
			t.Errorf("%s: got %v, want %v", c.name, c.got, c.want)
		}
	}
}

func TestATimedOutProbeKeepsTheLastRTTAndAViaChangeReplacesIt(t *testing.T) {
	m := NewProbeMetrics(prometheus.NewRegistry())
	_ = m.Apply([]byte(probeOK))
	lost := strings.NewReplacer(`"delivered": true`, `"delivered": false`, `"rtt_s": 2.5`, `"rtt_s": null`,
		`"sent_total": 10`, `"sent_total": 11`).Replace(probeOK)
	if err := m.Apply([]byte(lost)); err != nil {
		t.Fatal(err)
	}
	if got := testutil.ToFloat64(m.success.WithLabelValues("0a0b0c0d")); got != 0 {
		t.Errorf("success after a timeout: %v", got)
	}
	if got := testutil.ToFloat64(m.rtt.WithLabelValues("0a0b0c0d", "lora")); got != 2.5 {
		t.Errorf("rtt after a timeout: %v, want the last delivered", got)
	}
	udp := strings.NewReplacer(`"via": "lora"`, `"via": "udp"`, `"rtt_s": 2.5`, `"rtt_s": 0.04`,
		`"sent_total": 10`, `"sent_total": 12`, `"delivered_total": 9`, `"delivered_total": 10`).Replace(probeOK)
	_ = m.Apply([]byte(udp))
	if n := testutil.CollectAndCount(m.rtt); n != 1 {
		t.Errorf("rtt series: %d, want only the current interface's", n)
	}
}

func TestAMalformedProbeIsRefused(t *testing.T) {
	m := NewProbeMetrics(prometheus.NewRegistry())
	for _, bad := range []string{
		`{"v": 1}`,
		strings.Replace(probeOK, `"rtt_s": 2.5`, `"rtt_s": null`, 1),
		strings.Replace(probeOK, `"delivered_total": 9`, `"delivered_total": 11`, 1),
		strings.Replace(probeOK, `"sender": "0a0b0c0d"`, `"sender": "RAD"`, 1),
		strings.Replace(probeOK, `, "via": "lora"`, ``, 1),
	} {
		if err := m.Apply([]byte(bad)); err == nil {
			t.Errorf("accepted %.50q", bad)
		}
	}
	if got := testutil.ToFloat64(m.refused); got != 5 {
		t.Errorf("refused %v, want 5", got)
	}
}
