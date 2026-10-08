package main

import (
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"testing"

	"github.com/klauspost/compress/snappy"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"google.golang.org/protobuf/encoding/protowire"
)

// decoded is one series as the receiver sees it.
type decoded struct {
	labels map[string]string
	value  float64
	ms     int64
}

// decodeWriteRequest reads what encodeWriteRequest wrote, field by field, the
// way a remote-write receiver does.
func decodeWriteRequest(t *testing.T, b []byte) []decoded {
	var out []decoded
	for len(b) > 0 {
		_, _, n := protowire.ConsumeTag(b)
		b = b[n:]
		ts, n := protowire.ConsumeBytes(b)
		b = b[n:]
		d := decoded{labels: map[string]string{}}
		var names []string
		for len(ts) > 0 {
			num, _, n := protowire.ConsumeTag(ts)
			ts = ts[n:]
			body, n := protowire.ConsumeBytes(ts)
			ts = ts[n:]
			if num == 1 {
				_, _, n := protowire.ConsumeTag(body)
				name, m := protowire.ConsumeString(body[n:])
				rest := body[n+m:]
				_, _, n = protowire.ConsumeTag(rest)
				value, _ := protowire.ConsumeString(rest[n:])
				d.labels[name] = value
				names = append(names, name)
			} else {
				_, _, n := protowire.ConsumeTag(body)
				bits, m := protowire.ConsumeFixed64(body[n:])
				d.value = math.Float64frombits(bits)
				rest := body[n+m:]
				_, _, n = protowire.ConsumeTag(rest)
				ms, _ := protowire.ConsumeVarint(rest[n:])
				d.ms = int64(ms)
			}
		}
		if !sort.StringsAreSorted(names) {
			t.Errorf("labels not sorted: %v", names)
		}
		out = append(out, d)
	}
	return out
}

func receiver(t *testing.T, got *[]decoded) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Content-Encoding") != "snappy" {
			t.Errorf("Content-Encoding %q", r.Header.Get("Content-Encoding"))
		}
		body, _ := io.ReadAll(r.Body)
		raw, err := snappy.Decode(nil, body)
		if err != nil {
			t.Fatal(err)
		}
		*got = append(*got, decodeWriteRequest(t, raw)...)
		w.WriteHeader(http.StatusNoContent)
	}))
}

func find(series []decoded, name string, labels ...string) *decoded {
	for i := range series {
		if series[i].labels["__name__"] != name {
			continue
		}
		ok := true
		for j := 0; j+1 < len(labels); j += 2 {
			if series[i].labels[labels[j]] != labels[j+1] {
				ok = false
			}
		}
		if ok {
			return &series[i]
		}
	}
	return nil
}

func TestABackfilledHealthReportIsWrittenAtItsOwnTime(t *testing.T) {
	var got []decoded
	srv := receiver(t, &got)
	defer srv.Close()
	b := NewBackfill(prometheus.NewRegistry(), srv.URL)
	msg := strings.Replace(full, `"v": 1,`, `"v": 1, "kind": "health", "backfill": {"exact": true, "collected_at": 1790999999},`, 1)
	if err := b.Apply([]byte(msg)); err != nil {
		t.Fatal(err)
	}
	up := find(got, "mesh_board_uptime_seconds", "sender", "0a0b0c0d", "backfill", "exact")
	if up == nil || up.value != 3600 || up.ms != 1790949297656 {
		t.Fatalf("uptime sample: %+v", up)
	}
	if s := find(got, "mesh_board_interface_up", "interface", "lora"); s == nil || s.value != 1 {
		t.Errorf("lora up: %+v", s)
	}
	if s := find(got, "mesh_board_report_received_timestamp_seconds"); s != nil {
		t.Errorf("a backfill must not move the board's last-report time: %+v", s)
	}
	if n := testutil.ToFloat64(b.written); n != float64(len(got)) {
		t.Errorf("written %v, received %d", n, len(got))
	}
}

func TestABackfilledDetailReportCarriesItsLinks(t *testing.T) {
	var got []decoded
	srv := receiver(t, &got)
	defer srv.Close()
	b := NewBackfill(prometheus.NewRegistry(), srv.URL)
	msg := strings.Replace(detailFull, `"v": 1,`, `"v": 1, "kind": "detail", "backfill": {"exact": false, "collected_at": 2000},`, 1)
	if err := b.Apply([]byte(msg)); err != nil {
		t.Fatal(err)
	}
	link := find(got, "mesh_link_heard_timestamp_seconds", "neighbour", "11223344", "backfill", "approximate")
	if link == nil || link.value != 940 || link.ms != 1000000 || link.labels["neighbour_name"] != "Ayse" {
		t.Fatalf("link sample: %+v", link)
	}
	if s := find(got, "mesh_board_interface_tx_bytes_total", "interface", "tcp_client"); s == nil || s.value != 7 {
		t.Errorf("tcp clients summed: %+v", s)
	}
}

func TestABackfillPrometheusRefusesIsCountedAndReported(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "out of bounds", http.StatusBadRequest)
	}))
	defer srv.Close()
	b := NewBackfill(prometheus.NewRegistry(), srv.URL)
	msg := strings.Replace(full, `"v": 1,`, `"v": 1, "kind": "health", "backfill": {"exact": true, "collected_at": 1},`, 1)
	if err := b.Apply([]byte(msg)); err == nil || !strings.Contains(err.Error(), "out of bounds") {
		t.Fatalf("got %v", err)
	}
	for _, bad := range []string{full, `{"kind": "health", "backfill": {"exact": true}}`,
		strings.Replace(full, `"v": 1,`, `"v": 1, "kind": "other", "backfill": {"exact": true},`, 1)} {
		if err := b.Apply([]byte(bad)); err == nil {
			t.Errorf("accepted %.40q", bad)
		}
	}
	if got := testutil.ToFloat64(b.refused); got != 4 {
		t.Errorf("refused %v, want 4", got)
	}
}
