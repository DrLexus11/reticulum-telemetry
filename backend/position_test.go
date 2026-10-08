package main

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

const positionOK = `{"v": 1, "sender": "0a0b0c0d", "name": "RAD-1", "lat": 41.01, "lon": 29.02, "source": "configured", "at": 1}`

func TestAPositionIsSetBySourceAndClearedByAnEmptyMessage(t *testing.T) {
	m := NewPositionMetrics(prometheus.NewRegistry())
	topic := "mesh/telemetry/0a0b0c0d/position"
	if err := m.Apply(topic, []byte(positionOK)); err != nil {
		t.Fatal(err)
	}
	if got := testutil.ToFloat64(m.lat.WithLabelValues("0a0b0c0d", "configured")); got != 41.01 {
		t.Errorf("lat %v", got)
	}
	if err := m.Apply(topic, nil); err != nil {
		t.Fatal(err)
	}
	if n := testutil.CollectAndCount(m.lat) + testutil.CollectAndCount(m.lon); n != 0 {
		t.Errorf("%d series after the position was cleared", n)
	}
}

func TestABadPositionIsRefused(t *testing.T) {
	m := NewPositionMetrics(prometheus.NewRegistry())
	for topic, payload := range map[string]string{
		"mesh/telemetry/0a0b0c0d/position": `{"v": 1, "sender": "0a0b0c0d", "lat": 95, "lon": 29, "source": "configured"}`,
		"mesh/telemetry/11223344/position": positionOK, // sender and topic disagree
		"mesh/telemetry/0a0b0c0e/position": `{"v": 1, "sender": "0a0b0c0e", "lat": 1, "lon": 2, "source": "guess"}`,
		"mesh/position":                    positionOK,
	} {
		if err := m.Apply(topic, []byte(payload)); err == nil {
			t.Errorf("accepted %s %.40q", topic, payload)
		}
	}
	if got := testutil.ToFloat64(m.refused); got != 4 {
		t.Errorf("refused %v, want 4", got)
	}
}
