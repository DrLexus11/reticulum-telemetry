package main

import (
	"encoding/json"
	"fmt"
	"regexp"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
)

// Detail is one board detail report as the gateway publishes it on
// mesh/telemetry/<sender>/detail (gateway/report.py, detail_to_message).
type Detail struct {
	V          int     `json:"v"`
	Sender     string  `json:"sender"`
	Name       *string `json:"name"`
	ReceivedAt float64 `json:"received_at"`
	UptimeS    float64 `json:"uptime_s"`
	Firmware   struct {
		Hash    string  `json:"hash"`
		Version string  `json:"version"`
		Env     *string `json:"env"`
	} `json:"firmware"`
	Interfaces []struct {
		Interface string  `json:"interface"`
		Up        bool    `json:"up"`
		RxBytes   float64 `json:"rx_bytes"`
		TxBytes   float64 `json:"tx_bytes"`
	} `json:"interfaces"`
	Radio *struct {
		RSSI        *float64 `json:"rssi_dbm"`
		SNR         float64  `json:"snr_db"`
		Noise       *float64 `json:"noise_dbm"`
		Utilisation float64  `json:"utilisation_pct"`
		Airtime     float64  `json:"airtime_pct"`
	} `json:"radio"`
	Propagation *struct {
		Messages   float64  `json:"messages"`
		Bytes      float64  `json:"bytes"`
		Peers      float64  `json:"peers"`
		SyncOK     float64  `json:"sync_ok"`
		SyncFailed float64  `json:"sync_failed"`
		LastSyncS  *float64 `json:"last_sync_s"`
	} `json:"propagation"`
	Neighbours []struct {
		Node      string   `json:"node"`
		Name      *string  `json:"name"`
		Interface string   `json:"interface"`
		RSSI      *float64 `json:"rssi_dbm"`
		HeardS    float64  `json:"heard_s"`
	} `json:"neighbours"`
	NeighboursTruncated bool `json:"neighbours_truncated"`
}

// A link's labels: who heard whom over what, and both ends' names (the id
// until a name is heard), so a matrix or a graph needs no join.
var linkLabels = []string{"sender", "neighbour", "interface", "sender_name", "neighbour_name"}

// Every field the backend reads must be present: encoding/json would
// zero-fill a missing one, and a zero is a real value on a dashboard. The
// nullable ones must be present too, as null.
var (
	detailRequired    = []string{"v", "sender", "received_at", "uptime_s", "firmware", "interfaces", "neighbours", "neighbours_truncated"}
	detailNullable    = []string{"name", "radio", "propagation"}
	firmwareRequired  = []string{"hash", "version"}
	firmwareNullable  = []string{"env"}
	interfaceRequired = []string{"interface", "up", "rx_bytes", "tx_bytes"}
	radioRequired     = []string{"snr_db", "utilisation_pct", "airtime_pct"}
	radioNullable     = []string{"rssi_dbm", "noise_dbm"}
	storeRequired     = []string{"messages", "bytes", "peers", "sync_ok", "sync_failed"}
	storeNullable     = []string{"last_sync_s"}
	neighbourRequired = []string{"node", "interface", "heard_s"}
	neighbourNullable = []string{"name", "rssi_dbm"}
)

var hashPattern = regexp.MustCompile(`^[0-9a-f]{8}$`)

// fieldsOf checks one JSON object: `required` present and not null,
// `nullable` present. It returns the object's fields for nested checks.
func fieldsOf(raw json.RawMessage, where string, required, nullable []string) (map[string]json.RawMessage, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return nil, fmt.Errorf("%s is not an object", where)
	}
	for _, name := range required {
		if v, ok := fields[name]; !ok || string(v) == "null" {
			return nil, fmt.Errorf("%s: required field %q missing", where, name)
		}
	}
	for _, name := range nullable {
		if _, ok := fields[name]; !ok {
			return nil, fmt.Errorf("%s: field %q absent (null if unknown)", where, name)
		}
	}
	return fields, nil
}

func listOf(raw json.RawMessage, where string, required, nullable []string) error {
	var items []json.RawMessage
	if err := json.Unmarshal(raw, &items); err != nil {
		return fmt.Errorf("%s is not a list", where)
	}
	for i, item := range items {
		if _, err := fieldsOf(item, fmt.Sprintf("%s[%d]", where, i), required, nullable); err != nil {
			return err
		}
	}
	return nil
}

func nonNegative(values ...float64) bool {
	for _, v := range values {
		if v < 0 {
			return false
		}
	}
	return true
}

func validateDetail(payload []byte) (Detail, error) {
	var d Detail
	fields, err := fieldsOf(payload, "report", detailRequired, detailNullable)
	if err != nil {
		return d, err
	}
	if _, err := fieldsOf(fields["firmware"], "firmware", firmwareRequired, firmwareNullable); err != nil {
		return d, err
	}
	if err := listOf(fields["interfaces"], "interfaces", interfaceRequired, nil); err != nil {
		return d, err
	}
	if err := listOf(fields["neighbours"], "neighbours", neighbourRequired, neighbourNullable); err != nil {
		return d, err
	}
	if string(fields["radio"]) != "null" {
		if _, err := fieldsOf(fields["radio"], "radio", radioRequired, radioNullable); err != nil {
			return d, err
		}
	}
	if string(fields["propagation"]) != "null" {
		if _, err := fieldsOf(fields["propagation"], "propagation", storeRequired, storeNullable); err != nil {
			return d, err
		}
	}
	if err := json.Unmarshal(payload, &d); err != nil {
		return d, err
	}
	if d.V != 1 {
		return d, fmt.Errorf("version %d, not 1", d.V)
	}
	if !senderPattern.MatchString(d.Sender) {
		return d, fmt.Errorf("sender %q is not 8 lower-case hex digits", d.Sender)
	}
	if !hashPattern.MatchString(d.Firmware.Hash) {
		return d, fmt.Errorf("firmware hash %q is not 8 lower-case hex digits", d.Firmware.Hash)
	}
	if !nonNegative(d.ReceivedAt, d.UptimeS) {
		return d, fmt.Errorf("negative time")
	}
	for _, f := range d.Interfaces {
		if f.Interface == "" || !nonNegative(f.RxBytes, f.TxBytes) {
			return d, fmt.Errorf("interface %q: unnamed, or a negative count", f.Interface)
		}
	}
	if r := d.Radio; r != nil {
		if r.Utilisation < 0 || r.Utilisation > 100 || r.Airtime < 0 || r.Airtime > 100 {
			return d, fmt.Errorf("radio percentage out of range")
		}
	}
	if p := d.Propagation; p != nil {
		if !nonNegative(p.Messages, p.Bytes, p.Peers, p.SyncOK, p.SyncFailed) ||
			(p.LastSyncS != nil && *p.LastSyncS < 0) {
			return d, fmt.Errorf("negative propagation count")
		}
	}
	for _, n := range d.Neighbours {
		if !senderPattern.MatchString(n.Node) {
			return d, fmt.Errorf("neighbour %q is not 8 lower-case hex digits", n.Node)
		}
		if n.Interface == "" || n.HeardS < 0 {
			return d, fmt.Errorf("neighbour %s: no interface, or a negative age", n.Node)
		}
	}
	return d, nil
}

// DetailMetrics holds the detail report's series. Lists -- interfaces and
// neighbours -- are replaced whole on each report: a carrier or a neighbour
// that is gone loses its series rather than keeping its last value forever.
type DetailMetrics struct {
	received, firmware                                *prometheus.GaugeVec
	ifUp, ifRx, ifTx                                  *prometheus.GaugeVec
	rssi, snr, noise, utilisation, airtime            *prometheus.GaugeVec
	pnMessages, pnBytes, pnPeers, pnOK, pnFail, pnAge *prometheus.GaugeVec
	truncated                                         *prometheus.GaugeVec
	linkHeard, linkRSSI                               *prometheus.GaugeVec
	// nodeInfo names every node the mesh reports, boards and the phones they
	// hear alike: mesh_node_info{node, name}. One series per node.
	nodeInfo *prometheus.GaugeVec
	refused  prometheus.Counter

	mu         sync.Mutex
	firmwareOf map[string][]string
	ifsOf      map[string][]string
	linksOf    map[string][][]string
	nodeName   map[string]string
}

func nodeGauge(reg prometheus.Registerer, subsystem, name, help string, labels ...string) *prometheus.GaugeVec {
	g := prometheus.NewGaugeVec(prometheus.GaugeOpts{Namespace: "mesh", Subsystem: subsystem, Name: name, Help: help}, labels)
	reg.MustRegister(g)
	return g
}

func NewDetailMetrics(reg prometheus.Registerer) *DetailMetrics {
	return &DetailMetrics{
		received:    gauge(reg, "detail_received_timestamp_seconds", "When the gateway received this board's latest detail report."),
		firmware:    gauge(reg, "firmware_info", "1, labelled with the running image: hash (first 4 bytes), version, build environment.", "hash", "version", "env"),
		ifUp:        gauge(reg, "interface_online", "1 if the interface reports itself online (detail report kinds).", "interface"),
		ifRx:        gauge(reg, "interface_rx_bytes_total", "Bytes received on the interface since boot, as it counts them; take rate().", "interface"),
		ifTx:        gauge(reg, "interface_tx_bytes_total", "Bytes sent on the interface since boot, as it counts them; take rate().", "interface"),
		rssi:        gauge(reg, "radio_rssi_dbm", "LoRa RSSI of the last packet received."),
		snr:         gauge(reg, "radio_snr_db", "LoRa SNR of the last packet received."),
		noise:       gauge(reg, "radio_noise_floor_dbm", "LoRa noise floor."),
		utilisation: gauge(reg, "radio_channel_utilisation_percent", "LoRa channel utilisation, as the board measures it."),
		airtime:     gauge(reg, "radio_airtime_percent", "LoRa airtime this board used."),
		pnMessages:  gauge(reg, "pn_store_messages", "Messages in the board's LXMF propagation store."),
		pnBytes:     gauge(reg, "pn_store_bytes", "Size of that store (whole KB on the wire)."),
		pnPeers:     gauge(reg, "pn_peers", "Propagation peers the board syncs with."),
		pnOK:        gauge(reg, "pn_syncs_ok_total", "Peer syncs that completed since boot."),
		pnFail:      gauge(reg, "pn_syncs_failed_total", "Peer syncs that failed since boot."),
		pnAge:       gauge(reg, "pn_last_sync_age_seconds", "Seconds since the last completed sync, at report time; absent if none yet."),
		truncated:   gauge(reg, "neighbours_truncated", "1 if the board heard more neighbours than its report carries."),
		linkHeard: nodeGauge(reg, "link", "heard_timestamp_seconds",
			"When `sender` last heard `neighbour` directly, over `interface`: who sees whom.", linkLabels...),
		linkRSSI: nodeGauge(reg, "link", "rssi_dbm",
			"RSSI at which `sender` last heard `neighbour`, where the carrier measures one.", linkLabels...),
		nodeInfo: nodeGauge(reg, "node", "info", "1, labelled with the node's announced name -- its id until one is heard.", "node", "name"),
		refused: func() prometheus.Counter {
			c := prometheus.NewCounter(prometheus.CounterOpts{Namespace: "mesh", Name: "detail_reports_refused_total",
				Help: "Messages on the detail topics that were not a version 1 detail report."})
			reg.MustRegister(c)
			return c
		}(),
		firmwareOf: map[string][]string{},
		ifsOf:      map[string][]string{},
		linksOf:    map[string][][]string{},
		nodeName:   map[string]string{},
	}
}

// Apply validates one detail message and sets that board's series. A refused
// message leaves every series as it was.
func (m *DetailMetrics) Apply(payload []byte) error {
	d, err := validateDetail(payload)
	if err != nil {
		m.refused.Inc()
		return err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	s := d.Sender
	m.received.WithLabelValues(s).Set(d.ReceivedAt)

	env := ""
	if d.Firmware.Env != nil {
		env = *d.Firmware.Env
	}
	fw := []string{s, d.Firmware.Hash, d.Firmware.Version, env}
	if previous, ok := m.firmwareOf[s]; ok {
		m.firmware.DeleteLabelValues(previous...)
	}
	m.firmwareOf[s] = fw
	m.firmware.WithLabelValues(fw...).Set(1)

	// Interfaces: two of one kind (two TCP clients, say) are summed.
	for _, kind := range m.ifsOf[s] {
		m.ifUp.DeleteLabelValues(s, kind)
		m.ifRx.DeleteLabelValues(s, kind)
		m.ifTx.DeleteLabelValues(s, kind)
	}
	up := map[string]bool{}
	rx := map[string]float64{}
	tx := map[string]float64{}
	var kinds []string
	for _, f := range d.Interfaces {
		if _, seen := rx[f.Interface]; !seen {
			kinds = append(kinds, f.Interface)
		}
		up[f.Interface] = up[f.Interface] || f.Up
		rx[f.Interface] += f.RxBytes
		tx[f.Interface] += f.TxBytes
	}
	for _, kind := range kinds {
		m.ifUp.WithLabelValues(s, kind).Set(boolf(up[kind]))
		m.ifRx.WithLabelValues(s, kind).Set(rx[kind])
		m.ifTx.WithLabelValues(s, kind).Set(tx[kind])
	}
	m.ifsOf[s] = kinds

	setOrDelete := func(g *prometheus.GaugeVec, v *float64) {
		if v != nil {
			g.WithLabelValues(s).Set(*v)
		} else {
			g.DeleteLabelValues(s)
		}
	}
	if r := d.Radio; r != nil {
		setOrDelete(m.rssi, r.RSSI)
		setOrDelete(m.noise, r.Noise)
		setOrDelete(m.snr, &r.SNR)
		setOrDelete(m.utilisation, &r.Utilisation)
		setOrDelete(m.airtime, &r.Airtime)
	} else {
		for _, g := range []*prometheus.GaugeVec{m.rssi, m.noise, m.snr, m.utilisation, m.airtime} {
			g.DeleteLabelValues(s)
		}
	}

	if p := d.Propagation; p != nil {
		setOrDelete(m.pnMessages, &p.Messages)
		setOrDelete(m.pnBytes, &p.Bytes)
		setOrDelete(m.pnPeers, &p.Peers)
		setOrDelete(m.pnOK, &p.SyncOK)
		setOrDelete(m.pnFail, &p.SyncFailed)
		setOrDelete(m.pnAge, p.LastSyncS)
	} else {
		for _, g := range []*prometheus.GaugeVec{m.pnMessages, m.pnBytes, m.pnPeers, m.pnOK, m.pnFail, m.pnAge} {
			g.DeleteLabelValues(s)
		}
	}
	m.truncated.WithLabelValues(s).Set(boolf(d.NeighboursTruncated))

	if d.Name != nil && *d.Name != "" {
		m.nameNode(s, *d.Name)
	} else {
		m.knowNode(s)
	}
	for _, n := range d.Neighbours {
		if n.Name != nil && *n.Name != "" {
			m.nameNode(n.Node, *n.Name)
		} else {
			m.knowNode(n.Node)
		}
	}
	for _, labels := range m.linksOf[s] {
		m.linkHeard.DeleteLabelValues(labels...)
		m.linkRSSI.DeleteLabelValues(labels...)
	}
	var links [][]string
	for _, n := range d.Neighbours {
		labels := []string{s, n.Node, n.Interface, m.nodeName[s], m.nodeName[n.Node]}
		links = append(links, labels)
		m.linkHeard.WithLabelValues(labels...).Set(d.ReceivedAt - n.HeardS)
		if n.RSSI != nil {
			m.linkRSSI.WithLabelValues(labels...).Set(*n.RSSI)
		}
	}
	m.linksOf[s] = links
	return nil
}

// knowNode gives a node not yet named an info series under its own id, so
// every node a link names is on the graph. Called with mu held.
func (m *DetailMetrics) knowNode(node string) {
	if _, ok := m.nodeName[node]; !ok {
		m.nameNode(node, node)
	}
}

// nameNode keeps one info series per node; a new name replaces the old.
// Called with mu held.
func (m *DetailMetrics) nameNode(node, name string) {
	if previous, ok := m.nodeName[node]; ok {
		if previous == name {
			return
		}
		m.nodeInfo.DeleteLabelValues(node, previous)
	}
	m.nodeName[node] = name
	m.nodeInfo.WithLabelValues(node, name).Set(1)
}
