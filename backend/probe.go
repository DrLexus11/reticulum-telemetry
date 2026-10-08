package main

import (
	"encoding/json"
	"fmt"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

// Probe is one reachability probe of a board by a gateway, as the gateway
// publishes it on mesh/telemetry/<sender>/probe (gateway/probes.py): a small
// packet to the board's rnstransport.probe destination, and whether its proof
// came back. Measured, not inferred -- the rest of the telemetry says a board
// is alive; this says traffic gets through to it, and how fast.
type Probe struct {
	V         int      `json:"v"`
	Sender    string   `json:"sender"`
	Name      *string  `json:"name"`
	At        float64  `json:"at"`
	Delivered bool     `json:"delivered"`
	RTT       *float64 `json:"rtt_s"`
	Hops      *int     `json:"hops"`
	Via       *string  `json:"via"`
	Gateway   *string  `json:"gateway"`
	Sent      float64  `json:"sent_total"`
	Received  float64  `json:"delivered_total"`
}

var probeRequired = []string{"v", "sender", "at", "delivered", "sent_total", "delivered_total"}

func validateProbe(payload []byte) (Probe, error) {
	var p Probe
	if _, err := fieldsOf(payload, "probe", probeRequired, []string{"rtt_s", "hops", "via"}); err != nil {
		return p, err
	}
	if err := json.Unmarshal(payload, &p); err != nil {
		return p, err
	}
	if p.V != 1 {
		return p, fmt.Errorf("version %d, not 1", p.V)
	}
	if !senderPattern.MatchString(p.Sender) {
		return p, fmt.Errorf("sender %q is not 8 lower-case hex digits", p.Sender)
	}
	if !nonNegative(p.At, p.Sent, p.Received) || p.Received > p.Sent || (p.RTT != nil && *p.RTT < 0) {
		return p, fmt.Errorf("negative or impossible counts")
	}
	if p.Delivered && p.RTT == nil {
		return p, fmt.Errorf("delivered without a round-trip time")
	}
	return p, nil
}

// ProbeMetrics: the latest probe per board, and the gateway's running counts
// (cumulative since the gateway started, so rate() and a restart behave as for
// any counter; a retained message replayed on reconnect changes nothing).
type ProbeMetrics struct {
	last, success, rtt, hops, sent, delivered *prometheus.GaugeVec
	refused                                   prometheus.Counter
	mu                                        sync.Mutex
	viaOf                                     map[string]string
}

func NewProbeMetrics(reg prometheus.Registerer) *ProbeMetrics {
	m := &ProbeMetrics{
		last:      gauge(reg, "probe_timestamp_seconds", "When the gateway last probed this board."),
		success:   gauge(reg, "probe_success", "1 if the latest probe's proof came back, 0 if it timed out."),
		rtt:       gauge(reg, "probe_rtt_seconds", "Round-trip time of the latest delivered probe, by the interface it left on.", "via"),
		hops:      gauge(reg, "probe_hops", "Hops to the board when it was last probed."),
		sent:      gauge(reg, "probes_sent_total", "Probes this gateway sent the board since it started; take rate()."),
		delivered: gauge(reg, "probes_delivered_total", "Probes whose proof came back; delivered/sent is reachability."),
		refused: func() prometheus.Counter {
			c := prometheus.NewCounter(prometheus.CounterOpts{Namespace: "mesh", Name: "probe_reports_refused_total",
				Help: "Messages on the probe topics that were not a version 1 probe."})
			reg.MustRegister(c)
			return c
		}(),
		viaOf: map[string]string{},
	}
	return m
}

func (m *ProbeMetrics) Apply(payload []byte) error {
	p, err := validateProbe(payload)
	if err != nil {
		m.refused.Inc()
		return err
	}
	s := p.Sender
	m.last.WithLabelValues(s).Set(p.At)
	m.success.WithLabelValues(s).Set(boolf(p.Delivered))
	m.sent.WithLabelValues(s).Set(p.Sent)
	m.delivered.WithLabelValues(s).Set(p.Received)
	if p.Hops != nil {
		m.hops.WithLabelValues(s).Set(float64(*p.Hops))
	} else {
		m.hops.DeleteLabelValues(s)
	}
	if p.Delivered {
		via := "unknown"
		if p.Via != nil && *p.Via != "" {
			via = *p.Via
		}
		m.mu.Lock()
		if previous, ok := m.viaOf[s]; ok && previous != via {
			m.rtt.DeleteLabelValues(s, previous)
		}
		m.viaOf[s] = via
		m.mu.Unlock()
		m.rtt.WithLabelValues(s, via).Set(*p.RTT)
	}
	return nil
}
