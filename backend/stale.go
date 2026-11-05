package main

import (
	"fmt"
	"os"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
)

// DefaultStaleAfter is three report intervals (300 s each): a board silent
// that long has stopped reporting, and its last values are history, not state.
const DefaultStaleAfter = 15 * time.Minute

// Staleness judges a report by the time the gateway received it (received_at),
// never by when the backend did: the broker replays a retained report on every
// reconnect, and a twelve-day-old one must not look live because it just
// arrived. A zero bound disables it.
type Staleness struct {
	After time.Duration
	Now   func() time.Time
}

func (s Staleness) stale(receivedAt float64) bool {
	if s.After <= 0 {
		return false
	}
	now := s.Now
	if now == nil {
		now = time.Now
	}
	age := float64(now().UnixNano())/1e9 - receivedAt
	return age > s.After.Seconds()
}

// staleAfterDefault is the -stale-after flag's default: TELEMETRY_STALE_AFTER
// (a Go duration, "15m") if set, else DefaultStaleAfter.
func staleAfterDefault() (time.Duration, error) {
	v := os.Getenv("TELEMETRY_STALE_AFTER")
	if v == "" {
		return DefaultStaleAfter, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil || d < 0 {
		return 0, fmt.Errorf("TELEMETRY_STALE_AFTER=%q is not a non-negative duration", v)
	}
	return d, nil
}

// pruningGatherer drops stale boards' series before every scrape, so nothing
// a scrape returns is older than the bound.
func pruningGatherer(g prometheus.Gatherer, prune ...func()) prometheus.Gatherer {
	return prometheus.GathererFunc(func() ([]*dto.MetricFamily, error) {
		for _, p := range prune {
			p()
		}
		return g.Gather()
	})
}
