package main

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

// Report is one board health report as the gateway publishes it
// (gateway/report.py). Pointers are the fields the board may not know: a nil
// is "not reported", which is not the same as zero.
type Report struct {
	V                 int      `json:"v"`
	Sender            string   `json:"sender"`
	Name              *string  `json:"name"`
	ReceivedAt        float64  `json:"received_at"`
	Hops              *int     `json:"hops"`
	Gateway           *string  `json:"gateway"`
	UptimeS           float64  `json:"uptime_s"`
	Reset             string   `json:"reset"`
	Boots             float64  `json:"boots"`
	Crashes           float64  `json:"crashes"`
	Panics            float64  `json:"panics"`
	HeapBytes         float64  `json:"heap_bytes"`
	LargestBytes      float64  `json:"largest_bytes"`
	PsramBytes        *float64 `json:"psram_bytes"`
	InterfacesPresent []string `json:"interfaces_present"`
	InterfacesUp      []string `json:"interfaces_up"`
	BLEPeers          float64  `json:"ble_peers"`
	ESPNowPeers       float64  `json:"espnow_peers"`
	Paths             float64  `json:"paths"`
	Nodes             float64  `json:"nodes"`
	Relaying          bool     `json:"relaying"`
	RelayExpected     bool     `json:"relay_expected"`
	TimeCurrent       bool     `json:"time_current"`
	BatteryMV         *float64 `json:"battery_mv"`
	BatteryPct        *float64 `json:"battery_pct"`
}

// Interfaces every board is judged on (CLAUDE.md, interface completeness):
// each gets a present and an up series, 0 or 1, so a carrier that is missing
// shows as a 0 rather than as no data.
var interfaces = []string{"lora", "ble", "wifi", "espnow", "halow"}

// The reset reasons the wire format names (TelemetryCodec.h).
var resets = []string{"unknown", "poweron", "software", "panic", "task_wdt", "int_wdt", "brownout", "external", "other"}

// Metrics holds every per-board series. One registry, labelled by sender.
type Metrics struct {
	received, uptime, boots, crashes, panics             *prometheus.GaugeVec
	heap, largest, psram, paths, nodes, blePeers, espnow *prometheus.GaugeVec
	relaying, relayExpected, timeCurrent, hops           *prometheus.GaugeVec
	batteryMV, batteryPct                                *prometheus.GaugeVec
	present, up, reset                                   *prometheus.GaugeVec
	// info carries the board's announced name as a label (1 always); queries
	// join it by sender, so a name does not multiply every other series.
	info    *prometheus.GaugeVec
	refused prometheus.Counter
	mu      sync.Mutex
	nameOf  map[string]string
}

func gauge(reg prometheus.Registerer, name, help string, labels ...string) *prometheus.GaugeVec {
	g := prometheus.NewGaugeVec(prometheus.GaugeOpts{Namespace: "mesh", Subsystem: "board", Name: name, Help: help},
		append([]string{"sender"}, labels...))
	reg.MustRegister(g)
	return g
}

func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		received:      gauge(reg, "report_received_timestamp_seconds", "When the gateway received this board's latest report."),
		uptime:        gauge(reg, "uptime_seconds", "Seconds since the board's current run began."),
		boots:         gauge(reg, "boots", "Boots since power was last applied."),
		crashes:       gauge(reg, "crashes", "Lifetime watchdog and brownout restarts, as the board counts them."),
		panics:        gauge(reg, "panics", "Lifetime exception restarts, as the board counts them."),
		heap:          gauge(reg, "heap_free_bytes", "malloc-usable internal heap free (whole KB on the wire)."),
		largest:       gauge(reg, "heap_largest_block_bytes", "Largest free block of that heap."),
		psram:         gauge(reg, "psram_free_bytes", "PSRAM free; absent on boards without PSRAM."),
		paths:         gauge(reg, "paths", "Path-table entries."),
		nodes:         gauge(reg, "nodes", "Distinct identities heard."),
		blePeers:      gauge(reg, "ble_peers", "Connected BLE peers."),
		espnow:        gauge(reg, "espnow_peers", "ESP-NOW peers."),
		relaying:      gauge(reg, "relaying", "1 while forwarding for others."),
		relayExpected: gauge(reg, "relay_expected", "1 when two or more carriers are up, so relaying is expected."),
		timeCurrent:   gauge(reg, "time_current", "1 when the clock was adopted from a live source."),
		hops:          gauge(reg, "hops_to_gateway", "Hops the latest report travelled to its gateway."),
		batteryMV:     gauge(reg, "battery_millivolts", "Battery voltage; absent on boards without a battery."),
		batteryPct:    gauge(reg, "battery_percent", "Battery charge; absent on boards without a battery."),
		present:       gauge(reg, "interface_present", "1 if the carrier is compiled in and initialised.", "interface"),
		up:            gauge(reg, "interface_up", "1 if the carrier is up and usable now.", "interface"),
		reset:         gauge(reg, "reset_reason", "1 for why the board's current run began, 0 for every other reason.", "reason"),
		info:          gauge(reg, "info", "1, labelled with the board's announced name (the sender id until one is heard).", "name"),
		nameOf:        map[string]string{},
		refused: prometheus.NewCounter(prometheus.CounterOpts{Namespace: "mesh", Name: "reports_refused_total",
			Help: "Messages on the telemetry topics that were not a version 1 report."}),
	}
	reg.MustRegister(m.refused)
	return m
}

func boolf(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// Fields a version 1 report always carries (gateway/report.py). Optional ones
// -- hops, gateway, psram, battery -- may be null; these may not be absent.
var required = []string{"v", "sender", "received_at", "uptime_s", "reset", "boots", "crashes",
	"panics", "heap_bytes", "largest_bytes", "interfaces_present", "interfaces_up", "ble_peers",
	"espnow_peers", "paths", "nodes", "relaying", "relay_expected", "time_current"}

var senderPattern = regexp.MustCompile(`^[0-9a-f]{8}$`)

// validate checks a message completely before anything is set: a message
// missing a field would otherwise zero that board's series.
func validate(payload []byte) (Report, error) {
	var r Report
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(payload, &fields); err != nil {
		return r, err
	}
	for _, name := range required {
		raw, ok := fields[name]
		if !ok || string(raw) == "null" {
			return r, fmt.Errorf("required field %q missing", name)
		}
	}
	if err := json.Unmarshal(payload, &r); err != nil {
		return r, err
	}
	if r.V != 1 {
		return r, fmt.Errorf("version %d, not 1", r.V)
	}
	if !senderPattern.MatchString(r.Sender) {
		return r, fmt.Errorf("sender %q is not 8 lower-case hex digits", r.Sender)
	}
	for _, v := range []float64{r.ReceivedAt, r.UptimeS, r.Boots, r.Crashes, r.Panics, r.HeapBytes,
		r.LargestBytes, r.BLEPeers, r.ESPNowPeers, r.Paths, r.Nodes} {
		if v < 0 {
			return r, fmt.Errorf("negative count or size")
		}
	}
	if !contains(resets, r.Reset) {
		return r, fmt.Errorf("unknown reset reason %q", r.Reset)
	}
	return r, nil
}

// Apply validates one MQTT payload and sets that board's series. A refused
// message leaves every series as it was: it never overwrites a good report.
func (m *Metrics) Apply(payload []byte) error {
	r, err := validate(payload)
	if err != nil {
		m.refused.Inc()
		return err
	}
	s := r.Sender
	m.received.WithLabelValues(s).Set(r.ReceivedAt)
	m.uptime.WithLabelValues(s).Set(r.UptimeS)
	m.boots.WithLabelValues(s).Set(r.Boots)
	m.crashes.WithLabelValues(s).Set(r.Crashes)
	m.panics.WithLabelValues(s).Set(r.Panics)
	m.heap.WithLabelValues(s).Set(r.HeapBytes)
	m.largest.WithLabelValues(s).Set(r.LargestBytes)
	m.paths.WithLabelValues(s).Set(r.Paths)
	m.nodes.WithLabelValues(s).Set(r.Nodes)
	m.blePeers.WithLabelValues(s).Set(r.BLEPeers)
	m.espnow.WithLabelValues(s).Set(r.ESPNowPeers)
	m.relaying.WithLabelValues(s).Set(boolf(r.Relaying))
	m.relayExpected.WithLabelValues(s).Set(boolf(r.RelayExpected))
	m.timeCurrent.WithLabelValues(s).Set(boolf(r.TimeCurrent))
	// Not reported: the series is removed, not set to zero -- "no battery" must
	// not draw as a flat battery.
	setOrDelete := func(g *prometheus.GaugeVec, v *float64) {
		if v == nil {
			g.DeleteLabelValues(s)
		} else {
			g.WithLabelValues(s).Set(*v)
		}
	}
	setOrDelete(m.psram, r.PsramBytes)
	setOrDelete(m.batteryMV, r.BatteryMV)
	setOrDelete(m.batteryPct, r.BatteryPct)
	if r.Hops == nil {
		m.hops.DeleteLabelValues(s)
	} else {
		m.hops.WithLabelValues(s).Set(float64(*r.Hops))
	}
	for _, i := range interfaces {
		m.present.WithLabelValues(s, i).Set(boolf(contains(r.InterfacesPresent, i)))
		m.up.WithLabelValues(s, i).Set(boolf(contains(r.InterfacesUp, i)))
	}
	for _, reason := range resets {
		m.reset.WithLabelValues(s, reason).Set(boolf(reason == r.Reset))
	}
	m.setName(s, r.Name)
	return nil
}

// setName keeps exactly one info series per board. A board not yet named is
// shown by its sender id; a name that was heard is kept if a later report
// arrives without one (a gateway restarted before it heard the announce).
func (m *Metrics) setName(sender string, name *string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	next := sender
	if name != nil && *name != "" {
		next = *name
	} else if previous, ok := m.nameOf[sender]; ok && previous != sender {
		next = previous
	}
	if previous, ok := m.nameOf[sender]; ok && previous != next {
		m.info.DeleteLabelValues(sender, previous)
	}
	m.nameOf[sender] = next
	m.info.WithLabelValues(sender, next).Set(1)
}
