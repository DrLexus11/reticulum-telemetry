package main

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

// Position is where a board is, as the gateway publishes it on
// mesh/telemetry/<sender>/position (gateway/positions.py): configured for
// now, measured ("gnss") once a board has a receiver. An empty retained
// message clears it.
type Position struct {
	V      int     `json:"v"`
	Sender string  `json:"sender"`
	Lat    float64 `json:"lat"`
	Lon    float64 `json:"lon"`
	Source string  `json:"source"`
}

var positionSources = map[string]bool{"configured": true, "gnss": true}

type PositionMetrics struct {
	lat, lon *prometheus.GaugeVec
	refused  prometheus.Counter
	mu       sync.Mutex
	sourceOf map[string]string
}

func NewPositionMetrics(reg prometheus.Registerer) *PositionMetrics {
	m := &PositionMetrics{
		lat: gauge(reg, "latitude_degrees", "Where the board is; `source` says whether configured or measured.", "source"),
		lon: gauge(reg, "longitude_degrees", "Where the board is; `source` says whether configured or measured.", "source"),
		refused: func() prometheus.Counter {
			c := prometheus.NewCounter(prometheus.CounterOpts{Namespace: "mesh", Name: "position_reports_refused_total",
				Help: "Messages on the position topics that were not a version 1 position."})
			reg.MustRegister(c)
			return c
		}(),
		sourceOf: map[string]string{},
	}
	return m
}

// senderOfTopic is the <sender> in mesh/telemetry/<sender>/position.
func senderOfTopic(topic string) string {
	parts := strings.Split(topic, "/")
	if len(parts) < 2 {
		return ""
	}
	return parts[len(parts)-2]
}

func (m *PositionMetrics) Apply(topic string, payload []byte) error {
	sender := senderOfTopic(topic)
	if !senderPattern.MatchString(sender) {
		m.refused.Inc()
		return fmt.Errorf("topic %q names no sender", topic)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if len(payload) == 0 { // the retained position cleared
		if previous, ok := m.sourceOf[sender]; ok {
			m.lat.DeleteLabelValues(sender, previous)
			m.lon.DeleteLabelValues(sender, previous)
			delete(m.sourceOf, sender)
		}
		return nil
	}
	var p Position
	if _, err := fieldsOf(payload, "position", []string{"v", "sender", "lat", "lon", "source"}, nil); err != nil {
		m.refused.Inc()
		return err
	}
	if err := json.Unmarshal(payload, &p); err != nil {
		m.refused.Inc()
		return err
	}
	if p.V != 1 || p.Sender != sender || !positionSources[p.Source] ||
		p.Lat < -90 || p.Lat > 90 || p.Lon < -180 || p.Lon > 180 {
		m.refused.Inc()
		return fmt.Errorf("not a valid position for %s", sender)
	}
	if previous, ok := m.sourceOf[sender]; ok && previous != p.Source {
		m.lat.DeleteLabelValues(sender, previous)
		m.lon.DeleteLabelValues(sender, previous)
	}
	m.sourceOf[sender] = p.Source
	m.lat.WithLabelValues(sender, p.Source).Set(p.Lat)
	m.lon.WithLabelValues(sender, p.Source).Set(p.Lon)
	return nil
}
