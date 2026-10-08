"""Reports a board could not deliver live, turned into MQTT messages (T2).

A board with no gateway in reach keeps its reports and, about hourly, sends
them as one LXMF message -- a batch (telemetry_batch_codec.py) -- through its
own propagation node. The gateway collects it later. Pure, so it is tested
without Reticulum or a broker; lxmf_inbox.py does the I/O.

The reports go to mesh/telemetry/<sender>/backfill, **not retained**, and never
to the board's live topic: an hour-old report published there would overwrite
the board's current state. Each message is the live message's shape
(report.py) with "received_at" set to when the report was *taken*, plus:

    "kind":     "health" or "detail"
    "backfill": {"exact": bool, "collected_at": float}

"exact" is false only when the board's clock was not set, so the time was
counted back from when the gateway collected the batch -- late by however long
it waited in a propagation store.
"""

import report
import telemetry_batch_codec as bc
import telemetry_codec as tc
import telemetry_detail_codec as dc

BACKFILL_SUFFIX = "/backfill"


def backfill_topic(sender_id):
    return report.topic(sender_id) + BACKFILL_SUFFIX


def messages(batch_bytes, collected_at, gateway=None, name_for=None, signer=None):
    """(topic, message) pairs for one batch, oldest first, and the reason it was
    refused (None when accepted).

    `signer` is the sender id of the identity that signed the LXMF message; a
    batch claiming to be another board's is refused. `name_for(sender_hex)`
    names boards and neighbours, as for live reports.
    """
    batch = bc.decode(batch_bytes)
    if batch is None:
        return [], "not a v1 telemetry batch"
    if signer is not None and signer != batch["sender_id"]:
        return [], "signed by %08x, claims %08x" % (signer, batch["sender_id"])
    name_for = name_for or (lambda _s: None)
    out = []
    for entry, (taken_at, exact) in zip(batch["entries"], bc.entry_times(batch, collected_at)):
        data = entry["report"]
        meta = {"exact": exact, "collected_at": round(collected_at, 3)}
        if data[0] == tc.WIRE_VERSION:
            t = tc.decode(data)
            if t is None or t.sender_id != batch["sender_id"]:
                continue
            m = report.to_message(t, taken_at, gateway=gateway, name=name_for(report.sender_hex(t.sender_id)))
            m["kind"] = "health"
        elif data[0] == dc.WIRE_VERSION:
            d = dc.decode(data)
            if d is None or d["sender_id"] != batch["sender_id"]:
                continue
            m = report.detail_to_message(d, taken_at, gateway=gateway,
                                         name=name_for(report.sender_hex(d["sender_id"])), name_for=name_for)
            m["kind"] = "detail"
        else:
            continue
        m["backfill"] = meta
        out.append((backfill_topic(batch["sender_id"]), m))
    return out, None
