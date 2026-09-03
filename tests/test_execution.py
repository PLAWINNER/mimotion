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


if __name__ == "__main__":
    unittest.main()
