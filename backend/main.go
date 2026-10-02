// The telemetry backend: board health reports from MQTT, as Prometheus metrics.
//
// It subscribes to mesh/telemetry/# on the broker the gateways publish to and
// serves /metrics for Prometheus. Retained messages mean a restarted backend
// has every board's latest report at once, not after each board's next one.
package main

import (
	"flag"
	"log"
	"net/http"
	"os"

	mqtt "github.com/eclipse/paho.mqtt.golang"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func main() {
	broker := flag.String("broker", "tcp://127.0.0.1:1883", "MQTT broker URL")
	topic := flag.String("topic", "mesh/telemetry/#", "topic filter the gateways publish under")
	listen := flag.String("listen", "127.0.0.1:9101", "address to serve /metrics on")
	flag.Parse()

	reg := prometheus.NewRegistry()
	metrics := NewMetrics(reg)

	opts := mqtt.NewClientOptions().AddBroker(*broker).SetClientID("telemetry-backend").
		SetAutoReconnect(true).SetConnectRetry(true)
	// Credentials, if the broker needs them, come from the environment --
	// never the command line, where they would show in a process listing.
	if user := os.Getenv("MQTT_USERNAME"); user != "" {
		opts.SetUsername(user).SetPassword(os.Getenv("MQTT_PASSWORD"))
	}
	opts.SetOnConnectHandler(func(c mqtt.Client) {
		log.Printf("broker %s connected; subscribing to %s", *broker, *topic)
		c.Subscribe(*topic, 1, func(_ mqtt.Client, m mqtt.Message) {
			if err := metrics.Apply(m.Payload()); err != nil {
				log.Printf("refused %s: %v", m.Topic(), err)
			}
		})
	})
	client := mqtt.NewClient(opts)
	client.Connect()

	http.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{}))
	log.Printf("serving /metrics on %s", *listen)
	log.Fatal(http.ListenAndServe(*listen, nil))
}
