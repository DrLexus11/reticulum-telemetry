package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"sort"
	"time"

	"github.com/klauspost/compress/snappy"
	"github.com/prometheus/client_golang/prometheus"
	"google.golang.org/protobuf/encoding/protowire"
)

// Backfill writes the reports a board kept while no gateway was in reach (T2)
// into Prometheus at the time they were taken, through its remote-write
// receiver -- not through the gauges, which hold each board's latest state.
// The gateway publishes them on mesh/telemetry/<sender>/backfill
// (gateway/backfill.py), in the live messages' shape plus "kind" and
// "backfill". The series carry the live names and labels plus
// backfill="exact" (or "approximate" when the board's clock was not set), so
// every `max by (sender)` on the dashboard fills the gap without a change.
//
// Prometheus accepts samples up to out_of_order_time_window old (48 h in both
// environments): a board partitioned for longer loses its oldest reports.
type Backfill struct {
	url     string
	client  *http.Client
	written prometheus.Counter
	refused prometheus.Counter
}

func NewBackfill(reg prometheus.Registerer, url string) *Backfill {
	b := &Backfill{
		url:    url,
		client: &http.Client{Timeout: 15 * time.Second},
		written: prometheus.NewCounter(prometheus.CounterOpts{Namespace: "mesh", Name: "backfill_samples_written_total",
			Help: "Samples from boards' kept reports written to Prometheus at their original time."}),
		refused: prometheus.NewCounter(prometheus.CounterOpts{Namespace: "mesh", Name: "backfill_reports_refused_total",
			Help: "Backfill messages that were not a valid report, or that Prometheus refused."}),
	}
	reg.MustRegister(b.written, b.refused)
	return b
}

// sample is one value of one series at one time.
type sample struct {
	labels map[string]string
	value  float64
	ms     int64
}

// Apply turns one backfill message into samples and writes them. A refused
// message writes nothing.
func (b *Backfill) Apply(payload []byte) error {
	var head struct {
		Kind     string `json:"kind"`
		Backfill *struct {
			Exact bool `json:"exact"`
		} `json:"backfill"`
	}
	if err := json.Unmarshal(payload, &head); err != nil || head.Backfill == nil {
		b.refused.Inc()
		return fmt.Errorf("not a backfill message")
	}
	quality := "exact"
	if !head.Backfill.Exact {
		quality = "approximate"
	}
	var samples []sample
	switch head.Kind {
	case "health":
		r, err := validate(payload)
		if err != nil {
			b.refused.Inc()
			return err
		}
		samples = healthSamples(r, quality)
	case "detail":
		d, err := validateDetail(payload)
		if err != nil {
			b.refused.Inc()
			return err
		}
		samples = detailSamples(d, quality)
	default:
		b.refused.Inc()
		return fmt.Errorf("unknown kind %q", head.Kind)
	}
	if err := b.write(samples); err != nil {
		b.refused.Inc()
		return err
	}
	b.written.Add(float64(len(samples)))
	return nil
}

func at(seconds float64) int64 { return int64(math.Round(seconds * 1000)) }

func boardSample(name, sender, quality string, value float64, ms int64, extra ...string) sample {
	l := map[string]string{"__name__": "mesh_board_" + name, "sender": sender, "backfill": quality}
	for i := 0; i+1 < len(extra); i += 2 {
		l[extra[i]] = extra[i+1]
	}
	return sample{labels: l, value: value, ms: ms}
}

func healthSamples(r Report, quality string) []sample {
	s, ms := r.Sender, at(r.ReceivedAt)
	out := []sample{
		boardSample("uptime_seconds", s, quality, r.UptimeS, ms),
		boardSample("boots", s, quality, r.Boots, ms),
		boardSample("crashes", s, quality, r.Crashes, ms),
		boardSample("panics", s, quality, r.Panics, ms),
		boardSample("heap_free_bytes", s, quality, r.HeapBytes, ms),
		boardSample("heap_largest_block_bytes", s, quality, r.LargestBytes, ms),
		boardSample("paths", s, quality, r.Paths, ms),
		boardSample("nodes", s, quality, r.Nodes, ms),
		boardSample("ble_peers", s, quality, r.BLEPeers, ms),
		boardSample("espnow_peers", s, quality, r.ESPNowPeers, ms),
		boardSample("relaying", s, quality, boolf(r.Relaying), ms),
		boardSample("time_current", s, quality, boolf(r.TimeCurrent), ms),
	}
	if r.PsramBytes != nil {
		out = append(out, boardSample("psram_free_bytes", s, quality, *r.PsramBytes, ms))
	}
	if r.BatteryMV != nil {
		out = append(out, boardSample("battery_millivolts", s, quality, *r.BatteryMV, ms))
	}
	if r.BatteryPct != nil {
		out = append(out, boardSample("battery_percent", s, quality, *r.BatteryPct, ms))
	}
	for _, i := range interfaces {
		out = append(out,
			boardSample("interface_present", s, quality, boolf(contains(r.InterfacesPresent, i)), ms, "interface", i),
			boardSample("interface_up", s, quality, boolf(contains(r.InterfacesUp, i)), ms, "interface", i))
	}
	return out
}

func detailSamples(d Detail, quality string) []sample {
	s, ms := d.Sender, at(d.ReceivedAt)
	var out []sample
	up := map[string]bool{}
	rx := map[string]float64{}
	tx := map[string]float64{}
	for _, f := range d.Interfaces {
		up[f.Interface] = up[f.Interface] || f.Up
		rx[f.Interface] += f.RxBytes
		tx[f.Interface] += f.TxBytes
	}
	for kind := range rx {
		out = append(out,
			boardSample("interface_online", s, quality, boolf(up[kind]), ms, "interface", kind),
			boardSample("interface_rx_bytes_total", s, quality, rx[kind], ms, "interface", kind),
			boardSample("interface_tx_bytes_total", s, quality, tx[kind], ms, "interface", kind))
	}
	if r := d.Radio; r != nil {
		out = append(out,
			boardSample("radio_snr_db", s, quality, r.SNR, ms),
			boardSample("radio_channel_utilisation_percent", s, quality, r.Utilisation, ms),
			boardSample("radio_airtime_percent", s, quality, r.Airtime, ms))
		if r.RSSI != nil {
			out = append(out, boardSample("radio_rssi_dbm", s, quality, *r.RSSI, ms))
		}
		if r.Noise != nil {
			out = append(out, boardSample("radio_noise_floor_dbm", s, quality, *r.Noise, ms))
		}
	}
	if p := d.Propagation; p != nil {
		out = append(out,
			boardSample("pn_store_messages", s, quality, p.Messages, ms),
			boardSample("pn_store_bytes", s, quality, p.Bytes, ms),
			boardSample("pn_peers", s, quality, p.Peers, ms),
			boardSample("pn_syncs_ok_total", s, quality, p.SyncOK, ms),
			boardSample("pn_syncs_failed_total", s, quality, p.SyncFailed, ms))
	}
	if y := d.System; y != nil {
		out = append(out,
			boardSample("lora_rx_packets_total", s, quality, y.LoraRx, ms),
			boardSample("lora_tx_packets_total", s, quality, y.LoraTx, ms))
		for _, opt := range []struct {
			name string
			v    *float64
		}{{"temperature_celsius", y.TemperatureC}, {"lora_crc_errors_total", y.LoraCRC},
			{"clock_age_seconds", y.ClockAge}, {"ifac_rejected_total", y.IFAC}} {
			if opt.v != nil {
				out = append(out, boardSample(opt.name, s, quality, *opt.v, ms))
			}
		}
	}
	senderName := s
	if d.Name != nil && *d.Name != "" {
		senderName = *d.Name
	}
	for _, n := range d.Neighbours {
		neighbourName := n.Node
		if n.Name != nil && *n.Name != "" {
			neighbourName = *n.Name
		}
		labels := map[string]string{"sender": s, "neighbour": n.Node, "interface": n.Interface,
			"sender_name": senderName, "neighbour_name": neighbourName, "backfill": quality}
		heard := copyLabels(labels)
		heard["__name__"] = "mesh_link_heard_timestamp_seconds"
		out = append(out, sample{labels: heard, value: d.ReceivedAt - n.HeardS, ms: ms})
		if n.RSSI != nil {
			rssi := copyLabels(labels)
			rssi["__name__"] = "mesh_link_rssi_dbm"
			out = append(out, sample{labels: rssi, value: *n.RSSI, ms: ms})
		}
	}
	return out
}

func copyLabels(in map[string]string) map[string]string {
	out := make(map[string]string, len(in)+1)
	for k, v := range in {
		out[k] = v
	}
	return out
}

// encodeWriteRequest is the remote-write protobuf, by hand (prompb's module
// is far larger than four messages): WriteRequest{1: TimeSeries}, TimeSeries
// {1: Label, 2: Sample}, Label{1: name, 2: value}, Sample{1: double value,
// 2: int64 timestamp_ms}. Labels sorted by name, as the protocol requires.
func encodeWriteRequest(samples []sample) []byte {
	var req []byte
	for _, s := range samples {
		names := make([]string, 0, len(s.labels))
		for k := range s.labels {
			names = append(names, k)
		}
		sort.Strings(names)
		var ts []byte
		for _, k := range names {
			var l []byte
			l = protowire.AppendTag(l, 1, protowire.BytesType)
			l = protowire.AppendString(l, k)
			l = protowire.AppendTag(l, 2, protowire.BytesType)
			l = protowire.AppendString(l, s.labels[k])
			ts = protowire.AppendTag(ts, 1, protowire.BytesType)
			ts = protowire.AppendBytes(ts, l)
		}
		var sm []byte
		sm = protowire.AppendTag(sm, 1, protowire.Fixed64Type)
		sm = protowire.AppendFixed64(sm, math.Float64bits(s.value))
		sm = protowire.AppendTag(sm, 2, protowire.VarintType)
		sm = protowire.AppendVarint(sm, uint64(s.ms))
		ts = protowire.AppendTag(ts, 2, protowire.BytesType)
		ts = protowire.AppendBytes(ts, sm)
		req = protowire.AppendTag(req, 1, protowire.BytesType)
		req = protowire.AppendBytes(req, ts)
	}
	return req
}

func (b *Backfill) write(samples []sample) error {
	if len(samples) == 0 {
		return nil
	}
	body := snappy.Encode(nil, encodeWriteRequest(samples))
	req, err := http.NewRequest(http.MethodPost, b.url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Encoding", "snappy")
	req.Header.Set("Content-Type", "application/x-protobuf")
	req.Header.Set("X-Prometheus-Remote-Write-Version", "0.1.0")
	resp, err := b.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("remote write: %s: %s", resp.Status, bytes.TrimSpace(msg))
	}
	return nil
}
