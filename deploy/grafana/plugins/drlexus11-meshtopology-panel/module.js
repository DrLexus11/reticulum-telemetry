/*
 * Mesh topology -- a Grafana panel drawing who hears whom on the mesh.
 *
 * The look follows Crosstalk's network visualiser
 * (github.com/buildwithparallel/crosstalk, MIT, (c) 2024 Liam Cottle): the same
 * library (vis-network), physics, edge styling and icons (img/, with its
 * licence). Hand-written as an AMD module, so the plugin needs no build step:
 * Grafana loads module.js directly and supplies React and its own packages.
 *
 * Queries, as the dashboard sets them up (deploy/grafana/build_dashboard.py),
 * all instant and in table format:
 *   A  max by (node, name) (mesh_node_info)                         names of every node
 *   B  time() - max by (sender, neighbour, interface)
 *        (mesh_link_heard_timestamp_seconds)                         who hears whom, seconds since
 *   C  max by (sender, neighbour, interface) (mesh_link_rssi_dbm)    RSSI where measured
 *   D  time() - max by (sender) (mesh_board_report_received_timestamp_seconds)
 *                                                                    boards, seconds since their last report
 *   E  max by (sender, name) (mesh_board_info)                       board names
 */
define(["react", "@grafana/data", "@grafana/ui", "@grafana/runtime"],
function (React, grafanaData, grafanaUi, grafanaRuntime) {
  "use strict";

  var h = React.createElement;
  var PLUGIN_ID = "drlexus11-meshtopology-panel";

  // Fresh within 15 minutes, fading within an hour (a detail report comes every
  // 30), stale after. The same thresholds as the dashboard's tables.
  var LIVE_S = 900;
  var FADING_S = 3600;

  var CARRIERS = {
    lora:       { name: "LoRa",    color: "#b779ff" },
    ble_peer:   { name: "BLE",     color: "#2ee7c4" },
    espnow:     { name: "ESP-NOW", color: "#fbbf24" },
    udp:        { name: "UDP",     color: "#60a5fa" },
    tcp_server: { name: "TCP",     color: "#60a5fa" },
    tcp_client: { name: "TCP",     color: "#60a5fa" },
    auto:       { name: "Wi-Fi",   color: "#60a5fa" },
    halow:      { name: "HaLow",   color: "#f472b6" },
    serial:     { name: "Serial",  color: "#94a3b8" },
    other:      { name: "Other",   color: "#94a3b8" }
  };
  var LEGEND = [["LoRa", "#b779ff"], ["Wi-Fi / IP", "#60a5fa"], ["BLE", "#2ee7c4"],
                ["ESP-NOW", "#fbbf24"], ["HaLow", "#f472b6"]];

  function baseUrl() {
    var sub = (grafanaRuntime.config && grafanaRuntime.config.appSubUrl) || "";
    return sub + "/public/plugins/" + PLUGIN_ID + "/";
  }

  var visModule = null;
  function loadVis() {
    if (!visModule) visModule = import(baseUrl() + "vendor/vis-network.min.js");
    return visModule;
  }

  // ---- data: Grafana frames to rows ----------------------------------------

  function valuesOf(field) {
    var v = field.values;
    if (Array.isArray(v)) return v;
    if (v && typeof v.toArray === "function") return v.toArray();
    return [];
  }

  // One table frame to rows of {label: value, ..., value: number}.
  function rowsOf(frame) {
    var labels = [];
    var value = null;
    frame.fields.forEach(function (f) {
      if (f.type === "number") { if (!value) value = f; }
      else if (f.type === "string") labels.push(f);
    });
    var n = frame.length || (frame.fields.length ? valuesOf(frame.fields[0]).length : 0);
    var out = [];
    for (var i = 0; i < n; i++) {
      var row = { value: value ? valuesOf(value)[i] : null };
      labels.forEach(function (f) { row[f.name] = valuesOf(f)[i]; });
      out.push(row);
    }
    return out;
  }

  function rowsFor(series, refId) {
    var out = [];
    (series || []).forEach(function (frame) {
      if (frame.refId === refId) out = out.concat(rowsOf(frame));
    });
    return out;
  }

  function ago(seconds) {
    if (seconds === null || seconds === undefined || isNaN(seconds)) return "unknown";
    if (seconds < 90) return Math.round(seconds) + " s ago";
    if (seconds < 5400) return Math.round(seconds / 60) + " min ago";
    if (seconds < 172800) return (seconds / 3600).toFixed(1) + " h ago";
    return Math.round(seconds / 86400) + " d ago";
  }

  function carrier(kind) { return CARRIERS[kind] || CARRIERS.other; }

  function freshness(age) {
    if (age === null || age === undefined) return "stale";
    if (age < LIVE_S) return "live";
    if (age < FADING_S) return "fading";
    return "stale";
  }

  var STATUS = {
    live:   { border: "#2ee781", glow: "rgba(46, 231, 129, 0.32)" },
    fading: { border: "#fbbf24", glow: "rgba(251, 191, 36, 0.28)" },
    stale:  { border: "#ff5c72", glow: null }
  };

  function tooltip(lines) {
    var el = document.createElement("div");
    el.style.whiteSpace = "pre-line";
    el.textContent = lines.join("\n");
    return el;
  }

  // The graph: boards from their reports, everyone else from the links that
  // name them. Two directions over one carrier are one edge.
  function buildModel(series, theme, options) {
    var names = {};
    rowsFor(series, "A").forEach(function (r) { if (r.node) names[r.node] = r.name; });
    rowsFor(series, "E").forEach(function (r) { if (r.sender && r.name) names[r.sender] = r.name; });
    var boards = {};
    rowsFor(series, "D").forEach(function (r) { if (r.sender) boards[r.sender] = r.value; });
    var rssi = {};
    rowsFor(series, "C").forEach(function (r) {
      rssi[r.sender + "|" + r.neighbour + "|" + r.interface] = r.value;
    });

    var pairs = {};
    var heardAge = {};   // a non-board node: freshest time any board heard it
    rowsFor(series, "B").forEach(function (r) {
      if (!r.sender || !r.neighbour) return;
      var a = r.sender < r.neighbour ? r.sender : r.neighbour;
      var b = r.sender < r.neighbour ? r.neighbour : r.sender;
      var key = a + "|" + b + "|" + (r.interface || "other");
      var p = pairs[key] || (pairs[key] = { a: a, b: b, kind: r.interface || "other", dirs: [] });
      p.dirs.push({ from: r.neighbour, to: r.sender, age: r.value,
                    rssi: rssi[r.sender + "|" + r.neighbour + "|" + r.interface] });
      if (heardAge[r.neighbour] === undefined || r.value < heardAge[r.neighbour]) heardAge[r.neighbour] = r.value;
    });

    var font = {
      color: theme.isDark ? "#e8eaf4" : "#1f2433",
      background: theme.isDark ? "rgba(5, 6, 10, 0.78)" : "rgba(255, 255, 255, 0.85)",
      strokeWidth: 0,
      size: 13,
      vadjust: -1
    };

    var ids = {};
    Object.keys(boards).forEach(function (id) { ids[id] = true; });
    Object.keys(pairs).forEach(function (k) { ids[pairs[k].a] = true; ids[pairs[k].b] = true; });

    var nodes = Object.keys(ids).map(function (id) {
      var isBoard = boards[id] !== undefined;
      var age = isBoard ? boards[id] : heardAge[id];
      var state = STATUS[freshness(age)];
      var label = names[id] && names[id] !== id ? names[id] : id;
      var lines = [label, "id " + id];
      lines.push(isBoard ? "Board, last report " + ago(age) : "Heard directly " + ago(age));
      return {
        id: id,
        label: label,
        title: tooltip(lines),
        shape: "circularImage",
        image: baseUrl() + (isBoard ? "img/server.png" : "img/user.png"),
        size: isBoard ? 30 : 20,
        borderWidth: isBoard ? 2.5 : 1.5,
        color: {
          border: state.border,
          background: theme.isDark ? "#080b14" : "#ffffff",
          highlight: { border: "#6ea8ff", background: theme.isDark ? "#080b14" : "#ffffff" },
          hover: { border: "#7db0ff", background: theme.isDark ? "#080b14" : "#ffffff" }
        },
        shadow: state.glow ? { enabled: true, color: state.glow, size: isBoard ? 18 : 12, x: 0, y: 0 }
                           : { enabled: false },
        font: Object.assign({}, font, { size: isBoard ? 14 : 12 })
      };
    });

    var edges = Object.keys(pairs).map(function (key) {
      var p = pairs[key];
      var c = carrier(p.kind);
      var age = Math.min.apply(null, p.dirs.map(function (d) { return d.age; }));
      var fresh = freshness(age);
      var measured = p.dirs.filter(function (d) { return d.rssi !== undefined && d.rssi !== null; });
      var best = measured.length ? Math.max.apply(null, measured.map(function (d) { return d.rssi; })) : null;
      var oneWay = p.dirs.length === 1;
      var lines = p.dirs.map(function (d) {
        return (names[d.to] || d.to) + " hears " + (names[d.from] || d.from) + " over " + c.name +
          (d.rssi !== undefined && d.rssi !== null ? ", " + Math.round(d.rssi) + " dBm" : "") +
          ", " + ago(d.age);
      });
      var edge = {
        id: key,
        from: oneWay ? p.dirs[0].from : p.a,
        to: oneWay ? p.dirs[0].to : p.b,
        title: tooltip(lines),
        color: { color: c.color, opacity: fresh === "live" ? 0.85 : fresh === "fading" ? 0.55 : 0.3,
                 highlight: c.color, hover: c.color },
        width: fresh === "live" ? 2.4 : fresh === "fading" ? 1.5 : 1,
        dashes: fresh === "stale",
        arrows: { to: { enabled: oneWay, scaleFactor: 0.5 } },
        length: 240
      };
      if (options.edgeLabels) {
        edge.label = c.name + (best !== null ? " " + Math.round(best) + " dBm" : "");
        edge.font = Object.assign({}, font, { size: 11, align: "horizontal" });
      }
      return edge;
    });

    return { nodes: nodes, edges: edges };
  }

  // Crosstalk's network options (NetworkVisualiser.vue), themed for Grafana.
  function networkOptions(theme) {
    return {
      interaction: { tooltipDelay: 0, hover: true, hoverConnectedEdges: true,
                     keyboard: { enabled: false }, zoomSpeed: 0.65 },
      edges: {
        smooth: { enabled: false },   // straight: labels sit at the true midpoint (Crosstalk curves them)
        hoverWidth: 0.6,
        selectionWidth: 1
      },
      layout: { randomSeed: 1 },
      nodes: {
        borderWidthSelected: 2,
        shapeProperties: { interpolation: false }
      },
      physics: {
        barnesHut: { gravitationalConstant: -3400, centralGravity: 0.08, springLength: 165,
                     springConstant: 0.035, damping: 0.28, avoidOverlap: 0.55 },
        maxVelocity: 70,
        minVelocity: 0.35,
        stabilization: { enabled: true, iterations: 320, updateInterval: 40, fit: true }
      }
    };
  }

  function sync(dataset, items) {
    var keep = {};
    items.forEach(function (i) { keep[i.id] = true; });
    dataset.remove(dataset.getIds().filter(function (id) { return !keep[id]; }));
    dataset.update(items);
  }

  function Legend(props) {
    var theme = props.theme;
    var box = {
      position: "absolute", left: 8, bottom: 8, padding: "6px 8px", borderRadius: 6,
      fontSize: 11, lineHeight: "16px", pointerEvents: "none",
      background: theme.isDark ? "rgba(5, 6, 10, 0.72)" : "rgba(255, 255, 255, 0.85)",
      color: theme.colors.text.secondary
    };
    var swatch = function (color, dashed) {
      return h("span", { style: { display: "inline-block", width: 16, height: 0, marginRight: 6,
        verticalAlign: "middle", borderTop: "2px " + (dashed ? "dashed " : "solid ") + color } });
    };
    var dot = function (color) {
      return h("span", { style: { display: "inline-block", width: 9, height: 9, borderRadius: 5,
        marginRight: 6, verticalAlign: "middle", border: "2px solid " + color } });
    };
    return h("div", { style: box },
      LEGEND.map(function (l) { return h("div", { key: l[0] }, swatch(l[1]), l[0]); }),
      h("div", { style: { marginTop: 4 } }, dot(STATUS.live.border), "within 15 min"),
      h("div", null, dot(STATUS.fading.border), "within an hour"),
      h("div", null, dot(STATUS.stale.border), "older (dashed link)"));
  }

  function TopologyPanel(props) {
    var theme = grafanaUi.useTheme2();
    var container = React.useRef(null);
    var state = React.useRef({ network: null, nodes: null, edges: null, dark: null });
    var errorState = React.useState(null);
    var error = errorState[0];
    var setError = errorState[1];
    var countState = React.useState(0);
    var count = countState[0];
    var setCount = countState[1];

    React.useEffect(function () {
      var cancelled = false;
      loadVis().then(function (vis) {
        if (cancelled || !container.current) return;
        var model = buildModel(props.data.series, theme, props.options || {});
        var st = state.current;
        if (!st.network) {
          st.nodes = new vis.DataSet(model.nodes);
          st.edges = new vis.DataSet(model.edges);
          st.network = new vis.Network(container.current, { nodes: st.nodes, edges: st.edges },
                                       networkOptions(theme));
          st.dark = theme.isDark;
        } else {
          sync(st.nodes, model.nodes);
          sync(st.edges, model.edges);
          if (st.dark !== theme.isDark) {
            st.network.setOptions(networkOptions(theme));
            st.dark = theme.isDark;
          }
        }
        setCount(model.nodes.length);
        setError(null);
      }).catch(function (e) {
        if (!cancelled) setError("Could not load vis-network: " + e);
      });
      return function () { cancelled = true; };
    }, [props.data, theme.isDark, props.options && props.options.edgeLabels]);

    React.useEffect(function () {
      var st = state.current;
      if (st.network) st.network.setSize(props.width + "px", props.height + "px");
    }, [props.width, props.height]);

    React.useEffect(function () {
      return function () {
        var st = state.current;
        if (st.network) { st.network.destroy(); st.network = null; }
      };
    }, []);

    var note = null;
    if (error) note = error;
    else if (count === 0) note = "No links yet: boards report who they hear every 30 minutes.";

    return h("div", { style: { position: "relative", width: props.width, height: props.height } },
      h("div", { ref: container, style: { width: "100%", height: "100%" } }),
      h(Legend, { theme: theme }),
      note ? h("div", { style: { position: "absolute", top: 8, left: 8, fontSize: 12,
                                 color: theme.colors.text.secondary } }, note) : null);
  }

  var plugin = new grafanaData.PanelPlugin(TopologyPanel).setPanelOptions(function (builder) {
    return builder.addBooleanSwitch({
      path: "edgeLabels",
      name: "Carrier and RSSI on links",
      defaultValue: true
    });
  });

  return { plugin: plugin };
});
