import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import main
from tools import cloud_timer_setup as setup


class CloudTimerSetupTests(unittest.TestCase):
    def test_schedules_preserve_six_slots_and_automatic_mode(self):
        jobs = setup.job_specs("private-test-token")
        self.assertEqual(len(jobs), 8)
        primary = tuple((job["schedule"]["hours"][0], job["schedule"]["minutes"][0]) for job in jobs[:6])
        self.assertEqual(primary, main.DAILY_SLOTS)
        for job in jobs:
            self.assertEqual(job["schedule"]["timezone"], "Asia/Shanghai")
            self.assertTrue(json.loads(job["extendedData"]["body"])["inputs"]["scheduled"])
            self.assertEqual(job["url"], setup.DISPATCH_URL)
            self.assertFalse(job["saveResponses"])
        self.assertEqual(jobs[-1]["schedule"]["hours"], [22])
        self.assertEqual(jobs[-1]["schedule"]["minutes"], [7, 27])

    def test_setup_can_resume_without_duplicate_jobs_or_disk_secrets(self):
        remote_jobs = {}
        operations = []
        def fake_api(service, method, path, token, data=None):
            operations.append((service, method, path))
            if service == "github":
                return {"state": "active"} if method == "GET" else {}
            if method == "GET" and path == "/jobs":
                return {"jobs": [{"jobId": key, **value} for key, value in remote_jobs.items()]}
            if method == "PUT":
                job_id = len(remote_jobs) + 1
                remote_jobs[job_id] = data["job"]
                return {"jobId": job_id}
            job_id = int(path.split("/")[-1])
            if method == "PATCH":
                remote_jobs[job_id] = data["job"]
                return {}
            return {"jobDetails": remote_jobs[job_id]}
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(setup, "STATE_FILE", Path(directory) / "state.json"), \
                patch.object(setup, "api", side_effect=fake_api), \
                patch.object(setup.time, "sleep"):
            for _ in range(2):
                setup.LOCK.acquire()
                setup.configure("private-gh-token", "private-cron-key")
                self.assertEqual(setup.STATE["status"], "configured")
                state = setup.STATE_FILE.read_text(encoding="utf-8")
                self.assertNotIn("private-gh-token", state)
                self.assertNotIn("private-cron-key", state)
        self.assertEqual(len(remote_jobs), 8)
        self.assertEqual(sum(method == "PUT" for _, method, _ in operations), 8)
        self.assertEqual(sum(method == "PATCH" for _, method, _ in operations), 8)

    def test_local_form_rejects_cross_origin_submission(self):
        server = setup.ThreadingHTTPServer(("127.0.0.1", 0), setup.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            with urlopen(origin) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn(b'type="password"', response.read())
            body = json.dumps({"github": "github_pat_" + "x" * 30, "cron": "x" * 30}).encode()
            with patch.object(setup, "configure") as configure:
                request = Request(origin + "/configure", data=body, headers={
                    "Content-Type": "application/json", "Origin": "https://untrusted.invalid",
                    "X-Setup-Key": setup.SETUP_KEY})
                with self.assertRaises(HTTPError) as caught:
                    urlopen(request)
                self.assertEqual(caught.exception.code, 403)
                configure.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
