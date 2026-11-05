package main

import (
	"fmt"
	"os"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
)

// DefaultStaleAfter is three of the fleet's 300 s report intervals. The
// firmware accepts 60-3600 s ("Report Interval (s)"): the bound must exceed
// the longest interval any board is set to, or that board's health series
// vanish between its reports. A board's silence is shown by its received
// timestamp ("Silent", the silence alert) whatever the bound; the bound
// decides only how long its last values stay up after it falls silent.
const DefaultStaleAfter = 15 * time.Minute

// Freshness judges reports by the time the gateway received them
// (received_at), never by when the backend did: the broker replays a retained
// report on every reconnect, and a twelve-day-old one must not look live
// because it just arrived. One Freshness is shared by the health and detail
// metrics, so it knows when each board was last heard by either kind of
// report. A zero bound, or a nil *Freshness, never judges anything stale.
type Freshness struct {
	After time.Duration
	Now   func() time.Time

	mu     sync.Mutex
	latest map[string]float64
}

func NewFreshness(after time.Duration, now func() time.Time) *Freshness {
	return &Freshness{After: after, Now: now, latest: map[string]float64{}}
}

// Stale: received at `at`, older than the bound now.
func (f *Freshness) Stale(at float64) bool {
	if f == nil || f.After <= 0 {
		return false
	}
	now := f.Now
	if now == nil {
		now = time.Now
	}
	return float64(now().UnixNano())/1e9-at > f.After.Seconds()
}

// Heard records a report of either kind; an older one never lowers it.
func (f *Freshness) Heard(sender string, at float64) {
	if f == nil {
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if previous, ok := f.latest[sender]; !ok || at > previous {
		f.latest[sender] = at
	}
}

// Silent: nothing from this board, of either kind, within the bound.
func (f *Freshness) Silent(sender string) bool {
	if f == nil {
		return false
	}
	f.mu.Lock()
	at, ok := f.latest[sender]
	f.mu.Unlock()
	return ok && f.Stale(at)
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
