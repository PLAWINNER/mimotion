import contextlib
import io
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import main
from util import zepp_helper


class ExecutionTests(unittest.TestCase):
    def test_step_range_grows_until_the_evening_run(self):
        with patch.object(main, "config", {"MIN_STEP": "58000", "MAX_STEP": "66000"}, create=True):
            self.assertEqual(main.get_step_range_by_time(9, 53), (27296, 31061))
            self.assertEqual(main.get_step_range_by_time(15, 53), (43868, 49919))
            self.assertEqual(main.get_step_range_by_time(21, 53), (58000, 66000))

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
        with patch.object(zepp_helper.requests, "request") as request, \
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
                contextlib.redirect_stdout(output):
            result = main.run_single_account(1, 0, "test@example.invalid", "private-password")
        self.assertFalse(result["success"])
        self.assertEqual(result["msg"], "执行异常:ConnectionError")
        for private in ("private-token", "private-password", "https://"):
            self.assertNotIn(private, output.getvalue())
            self.assertNotIn(private, result["msg"])

    def test_app_token_check_retries_a_temporary_connection_failure(self):
        response = Mock(status_code=200)
        response.json.return_value = {"message": "success"}
        with patch.object(
            zepp_helper.requests,
            "request",
            side_effect=[zepp_helper.requests.ConnectionError("temporary"), response],
        ) as request, patch.object(zepp_helper.time, "sleep") as sleep:
            self.assertEqual(zepp_helper.check_app_token("unused"), (True, None))
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(5)

    def test_post_retries_keep_payload_and_stop_after_three_attempts(self):
        response = Mock(status_code=200)
        response.json.return_value = {"message": "success"}
        error = zepp_helper.requests.ConnectionError("private-token private-password")
        for failures, success in ((1, True), (3, False)):
            output = io.StringIO()
            replies = [error] * failures + ([response] if success else [])
            with self.subTest(failures=failures), \
                    patch.object(zepp_helper.requests, "request", side_effect=replies) as request, \
                    patch.object(zepp_helper.time, "sleep"), contextlib.redirect_stdout(output):
                if success:
                    self.assertEqual(zepp_helper.post_fake_brand_data("59000", "private-token", "user"), (True, "success"))
                else:
                    with self.assertRaises(zepp_helper.requests.ConnectionError):
                        zepp_helper.post_fake_brand_data("59000", "private-token", "user")
            self.assertEqual(request.call_count, 2 if success else 3)
            self.assertTrue(all(call == request.call_args_list[0] for call in request.call_args_list))
            self.assertEqual(request.call_args.kwargs["timeout"], (10, 20))
            self.assertNotIn("private-token", output.getvalue())
            self.assertNotIn("private-password", output.getvalue())

    def test_http_errors_retry_only_transient_server_failures(self):
        for status, count in ((503, 3), (401, 1), (403, 1), (429, 1)):
            response = Mock(status_code=status)
            with self.subTest(status=status), \
                    patch.object(zepp_helper.requests, "request", return_value=response) as request, \
                    patch.object(zepp_helper.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                self.assertIs(zepp_helper.request_with_retry("GET", "https://example.invalid/?token=private"), response)
            self.assertEqual(request.call_count, count)

    def test_schedule_boundaries_and_delayed_runs(self):
        cases = ((1, 39, None), (8, 59, None), (9, 0, "09:00"), (11, 33, "09:00"),
                 (11, 34, "11:34"), (15, 0, "14:08"), (21, 50, "21:50"),
                 (22, 30, "21:50"), (22, 31, None), (23, 59, None))
        for hour, minute, slot in cases:
            with self.subTest(hour=hour, minute=minute):
                expected = f"2026-09-05T{slot}" if slot else None
                self.assertEqual(main.get_due_slot(datetime(2026, 9, 5, hour, minute)), expected)

    def test_catchup_skips_successful_accounts_and_retries_failed_accounts(self):
        tokens = {user: {"user_id": user, "bound_device_id": "device"} for user in ("a@b.c", "b@b.c")}
        with patch.multiple(main, create=True, user_tokens=tokens, config={"MIN_STEP": 58000, "MAX_STEP": 66000}), \
                patch.dict(main.os.environ, {"GITHUB_EVENT_NAME": "schedule"}), \
                patch.object(main, "get_beijing_time", return_value=datetime(2026, 9, 5, 14, 28)), \
                patch.object(main.MiMotionRunner, "login", return_value="unused") as login, \
                patch.object(zepp_helper, "post_fake_brand_data", side_effect=[(True, "success"), (False, "failure"), (True, "success")]) as post:
            self.assertTrue(main.MiMotionRunner("a@b.c", "unused").login_and_post_step()[1])
            self.assertFalse(main.MiMotionRunner("b@b.c", "unused").login_and_post_step()[1])
            self.assertNotIn("last_sync", tokens["b@b.c"])
            runner = main.MiMotionRunner("a@b.c", "unused")
            self.assertTrue(runner.login_and_post_step()[1])
            self.assertTrue(runner.skipped)
            self.assertTrue(main.MiMotionRunner("b@b.c", "unused").login_and_post_step()[1])
            self.assertEqual(post.call_count, 3)
            self.assertEqual(login.call_count, 3)
            self.assertEqual(tokens["b@b.c"]["last_sync"]["slot"], "2026-09-05T14:08")

    def test_late_jobs_do_not_login_and_daily_steps_do_not_decrease(self):
        tokens = {"a@b.c": {"bound_device_id": "device", "last_sync": {
            "date": "2026-09-05", "slot": "2026-09-05T19:16", "step": 61000,
        }}}
        with patch.multiple(main, create=True, user_tokens=tokens, config={"MIN_STEP": 58000, "MAX_STEP": 66000}), \
                patch.dict(main.os.environ, {"GITHUB_EVENT_NAME": "schedule"}), \
                patch.object(main, "get_beijing_time") as now, \
                patch.object(main.MiMotionRunner, "login", return_value="unused") as login, \
                patch.object(main.random, "randint", side_effect=[59000, 25000]), \
                patch.object(zepp_helper, "post_fake_brand_data", return_value=(True, "success")) as post:
            now.return_value = datetime(2026, 9, 6, 1, 39)
            runner = main.MiMotionRunner("a@b.c", "unused")
            self.assertTrue(runner.login_and_post_step()[1])
            self.assertTrue(runner.skipped)
            login.assert_not_called()
            now.return_value = datetime(2026, 9, 5, 21, 50)
            self.assertTrue(main.MiMotionRunner("a@b.c", "unused").login_and_post_step()[1])
            self.assertEqual(post.call_args.args[0], "61000")
            now.return_value = datetime(2026, 9, 6, 9, 0)
            self.assertTrue(main.MiMotionRunner("a@b.c", "unused").login_and_post_step()[1])
            self.assertEqual(post.call_args.args[0], "25000")

    def test_all_skipped_accounts_are_reported_without_notifications(self):
        output = io.StringIO()
        results = [{"success": True, "skipped": True, "msg": "already submitted"}]
        with patch.multiple(main, create=True, users="first", passwords="unused", use_concurrent=False,
                            encrypt_support=False, sleep_seconds=0, push_config=None), \
                patch.object(main, "run_single_account", side_effect=results), \
                patch.object(main.push_util, "push_results") as notify, \
                patch.dict(main.os.environ, {"GITHUB_STEP_SUMMARY": ""}), \
                contextlib.redirect_stdout(output):
            self.assertTrue(main.execute())
        self.assertIn("成功：0，失败：0，跳过：1", output.getvalue())
        notify.assert_not_called()

    def test_unchanged_cache_is_not_reencrypted(self):
        tokens = {"a@b.c": {"last_sync": {"step": 59000}}}
        with patch.object(main, "user_tokens", tokens, create=True), \
                patch.object(main.os.path, "exists", return_value=True), \
                patch.object(main, "prepare_user_tokens", return_value=tokens), \
                patch.object(main, "encrypt_data") as encrypt:
            main.persist_user_tokens()
        encrypt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
