import contextlib
import io
import unittest
from unittest.mock import patch

import main
from util import zepp_helper


class ExecutionTests(unittest.TestCase):
    def test_failed_account_makes_the_run_unsuccessful(self):
        for successes in ((True, True), (True, False), (False, False)):
            results = [{"success": success} for success in successes]
            with self.subTest(successes=successes), patch.multiple(
                main, create=True, users="first#second", passwords="one#two",
                use_concurrent=False, encrypt_support=False,
                sleep_seconds=0, push_config=None,
            ), patch.object(main, "run_single_account", side_effect=results), \
                    patch.object(main.push_util, "push_results"), \
                    patch.dict(main.os.environ, {"GITHUB_STEP_SUMMARY": ""}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.execute(), all(successes))

    def test_token_refresh_does_not_print_the_token(self):
        output = io.StringIO()
        with patch.object(zepp_helper.requests, "get") as request, \
                contextlib.redirect_stdout(output):
            request.return_value.status_code = 200
            request.return_value.json.return_value = {
                "result": "ok", "token_info": {"app_token": "private-test-token"},
            }
            self.assertEqual(zepp_helper.grant_app_token("unused"), ("private-test-token", None))
        self.assertNotIn("private-test-token", output.getvalue())

    def test_network_exception_does_not_expose_request_credentials(self):
        output = io.StringIO()
        error = zepp_helper.requests.ConnectionError(
            "Request failed: https://example.invalid/?login_token=private-token&password=private-password"
        )
        with patch.object(main.MiMotionRunner, "login_and_post_step", side_effect=error), \
                patch.multiple(main, create=True, min_step=8000, max_step=10000), \
                contextlib.redirect_stdout(output):
            result = main.run_single_account(1, 0, "test@example.invalid", "private-password")
        self.assertFalse(result["success"])
        self.assertEqual(result["msg"], "执行异常:ConnectionError")
        for private in ("private-token", "private-password", "https://"):
            self.assertNotIn(private, output.getvalue())
            self.assertNotIn(private, result["msg"])


if __name__ == "__main__":
    unittest.main()
