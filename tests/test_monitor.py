import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from collector import collect
from briareus_monitor import Queue, DAY_MS, load_config, send, NoRedirect, run


class MonitorTests(unittest.TestCase):
    def test_real_linux_resources(self):
        raw = collect()
        self.assertEqual(len(raw["cpu"]["counters"]), 8)
        self.assertGreater(raw["cpu"]["cores"], 0)
        self.assertGreater(raw["memory"]["totalBytes"], 0)
        self.assertTrue(any(disk["mount"] == "/" for disk in raw["disks"]))
        self.assertFalse(any(net["name"] == "lo" for net in raw["network"]))
        self.assertLessEqual(raw["memory"]["usedPercent"], 100)

    def test_durable_queue_retry_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(directory)
            queue.append(1, {"old": True})
            queue.append(DAY_MS + 2, {"current": True})
            def failing(*args):
                raise OSError("offline")
            with self.assertRaises(OSError):
                queue.flush({}, failing)
            queue.close()
            queue = Queue(directory)
            received = []
            queue.flush({}, lambda config, samples: received.extend(samples))
            self.assertEqual(received, [{"at": DAY_MS + 2, "raw": {"current": True}}])
            self.assertEqual(queue.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
            queue.close()

    def test_acknowledged_batches_are_deleted_but_failed_batch_stays(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(directory)
            for i in range(15):
                queue.append(i, {"i": i})
            sizes = []
            def sender(config, samples):
                sizes.append(len(samples))
                if len(sizes) == 2:
                    raise OSError("offline")
            with self.assertRaises(OSError):
                queue.flush({}, sender)
            self.assertEqual(sizes, [10, 5])
            self.assertEqual(queue.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 5)
            queue.close()

    def test_queue_is_bounded_to_1440_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(directory)
            for i in range(1445):
                queue.append(i, {"i": i})
            self.assertEqual(queue.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1440)
            queue.close()

    def test_config_requires_https_and_machine_token(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "config.json"
            config = {"endpoint": "https://core.example/api/v1/telemetry/ssh/1/samples", "token": "brm_" + "a" * 43}
            filename.write_text(json.dumps(config))
            self.assertEqual(load_config(filename), config)
            for endpoint in ["http://core.example/", "https://user:pass@core.example/api/v1/telemetry/ssh/1/samples", "https://core.example/?secret=x"]:
                filename.write_text(json.dumps({**config, "endpoint": endpoint}))
                with self.assertRaises(ValueError):
                    load_config(filename)
            filename.write_text(json.dumps({**config, "token": "operator-token"}))
            with self.assertRaises(ValueError):
                load_config(filename)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, None, None, "https://other.example"))

    def test_sender_requires_batch_acknowledgment(self):
        config = {"endpoint": "https://core.example/api/v1/telemetry/ssh/1/samples", "token": "brm_" + "a" * 43}
        with patch("urllib.request.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"received":1,"accepted":1}'
            send(config, [{"at": 1, "raw": {}}])
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.headers["Authorization"], "Bearer " + config["token"])
            response.read.return_value = b'{}'
            with self.assertRaises(ValueError):
                send(config, [{"at": 1}])

    def test_one_shot_runs_without_background_children(self):
        with tempfile.TemporaryDirectory() as directory, patch("briareus_monitor.send"), patch("briareus_monitor.Queue.flush") as flush:
            run({}, directory, threading.Event(), once=True)
            flush.assert_called_once()


if __name__ == "__main__":
    unittest.main()
