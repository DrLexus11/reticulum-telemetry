"""A durable hand-off between the LXMF inbox and MQTT (T2).

Once the LXMF delivery callback returns, the router tells the propagation node
it has the message, and the node deletes its copy -- which may be the only one.
So the callback only writes the batch here, flushed to disk, and returns; a
drain publishes it and deletes the file once the broker has acknowledged every
message from it. A gateway that stops mid-way publishes the rest on its next
start; a message published twice is harmless (the backend writes the same
sample at the same time).

Pure apart from the files: `publish(topic, message)` is given, and returns True
only once the broker has acknowledged.
"""

import json
import os
import tempfile
import threading


class Spool:
    def __init__(self, folder):
        self.folder = os.path.expanduser(folder)
        os.makedirs(self.folder, exist_ok=True)
        self._lock = threading.Lock()

    def put(self, batch_bytes, collected_at, signer):
        """Write one batch durably; returns its file name."""
        record = {"batch": batch_bytes.hex(), "collected_at": collected_at, "signer": signer}
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=".incoming.")
        with os.fdopen(fd, "w") as f:
            json.dump(record, f)
            f.flush()
            os.fsync(f.fileno())
        name = "%.6f-%08x.json" % (collected_at, signer)
        os.replace(tmp, os.path.join(self.folder, name))
        dir_fd = os.open(self.folder, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return name

    def pending(self):
        return sorted(n for n in os.listdir(self.folder) if n.endswith(".json") and not n.startswith("."))

    def drain(self, to_messages, publish, log=print):
        """Publish every spooled batch, oldest first. `to_messages(batch_bytes,
        collected_at, signer)` returns (pairs, refused). A batch is deleted once
        every message is acknowledged, or when it is refused (it never will be
        accepted); one that fails to publish stays for the next drain. Returns
        how many batches were published."""
        done = 0
        with self._lock:
            for name in self.pending():
                path = os.path.join(self.folder, name)
                try:
                    with open(path) as f:
                        record = json.load(f)
                    batch = bytes.fromhex(record["batch"])
                except (OSError, ValueError, KeyError) as error:
                    log("[spool] %s unreadable, kept aside: %s" % (name, error))
                    os.replace(path, path + ".bad")
                    continue
                pairs, refused = to_messages(batch, record["collected_at"], record["signer"])
                if refused:
                    log("[spool] refused a batch: %s" % refused)
                    os.remove(path)
                    continue
                if not all(publish(topic, message) for topic, message in pairs):
                    log("[spool] %s: the broker did not acknowledge; kept for the next drain" % name)
                    break
                os.remove(path)
                done += 1
        return done
