"""Unit tests for client/search_client.py.

No network access: HTTP calls are replaced with mocks.
Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "client"))

import requests  # noqa: E402

import search_client as sc  # noqa: E402

SAMPLE = {
    "query": "linux kernel",
    "results": [
        {
            "title": "  Linux kernel - Wikipedia ",
            "url": "https://en.wikipedia.org/wiki/Linux_kernel",
            "content": "The Linux kernel is a free and\n open-source, Unix-like kernel.",
            "engine": "duckduckgo",
            "engines": ["duckduckgo", "bing"],
            "category": "general",
        },
        {"title": "", "url": "https://kernel.org/", "engine": "bing"},
        {"title": "Image only", "url": "https://img.example/1", "engines": ["bing images"], "content": None},
    ],
    "answers": [{"answer": "6.2137119224 mi"}],
    "corrections": [],
    "infoboxes": [{"infobox": "Linux kernel"}],
    "suggestions": ["linux kernel source"],
    "unresponsive_engines": [["brave", "too many requests"]],
}


def fake_response(status=200, payload=None, headers=None, text=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.headers = headers or {"Content-Type": "application/json"}
    if text is not None:
        resp.json.side_effect = ValueError("not json")
    else:
        resp.json.return_value = payload
    return resp


class ParseResponseTests(unittest.TestCase):
    def test_parses_fields_and_normalizes_text(self):
        r = sc.parse_response(SAMPLE, elapsed=1.5)
        self.assertEqual(r.query, "linux kernel")
        self.assertEqual(len(r.results), 3)
        first = r.results[0]
        self.assertEqual(first.title, "Linux kernel - Wikipedia")
        self.assertEqual(first.engines, ["bing", "duckduckgo"])
        self.assertEqual(first.snippet, "The Linux kernel is a free and open-source, Unix-like kernel.")
        self.assertEqual(r.answers, ["6.2137119224 mi"])
        self.assertEqual(r.infobox_titles, ["Linux kernel"])
        self.assertEqual(r.unresponsive, [("brave", "too many requests")])

    def test_tolerates_missing_optional_fields(self):
        r = sc.parse_response(SAMPLE)
        self.assertEqual(r.results[1].title, "(no title)")
        self.assertEqual(r.results[1].engines, ["bing"])  # falls back to "engine"
        self.assertEqual(r.results[2].snippet, "")

    def test_rejects_payload_without_results(self):
        with self.assertRaises(sc.ClientError) as ctx:
            sc.parse_response({"error": "nope"})
        self.assertEqual(ctx.exception.exit_code, sc.EXIT_HTTP)


class FetchJsonTests(unittest.TestCase):
    def call(self, response=None, side_effect=None):
        session = mock.Mock()
        session.get.return_value = response
        if side_effect is not None:
            session.get.side_effect = side_effect
        return sc.fetch_json(session, "https://search.example", {"q": "x", "format": "json"}, 5, True)

    def assert_exit(self, code, **kwargs):
        with self.assertRaises(sc.ClientError) as ctx:
            self.call(**kwargs)
        self.assertEqual(ctx.exception.exit_code, code)
        return str(ctx.exception)

    def test_success_returns_payload(self):
        data, elapsed = self.call(response=fake_response(payload=SAMPLE))
        self.assertEqual(data["query"], "linux kernel")
        self.assertGreaterEqual(elapsed, 0)

    def test_http_errors_map_to_exit_codes(self):
        self.assert_exit(sc.EXIT_AUTH, response=fake_response(401))
        msg = self.assert_exit(sc.EXIT_RATE_LIMIT, response=fake_response(429, headers={"Retry-After": "42"}))
        self.assertIn("42", msg)
        self.assert_exit(sc.EXIT_HTTP, response=fake_response(403))
        self.assert_exit(sc.EXIT_HTTP, response=fake_response(502))

    def test_non_json_body(self):
        self.assert_exit(sc.EXIT_HTTP, response=fake_response(200, text="<html>", headers={"Content-Type": "text/html"}))

    def test_network_failures(self):
        self.assert_exit(sc.EXIT_CONNECTION, side_effect=requests.exceptions.ConnectionError("refused"))
        self.assert_exit(sc.EXIT_CONNECTION, side_effect=requests.exceptions.ReadTimeout("slow"))
        msg = self.assert_exit(sc.EXIT_CONNECTION, side_effect=requests.exceptions.SSLError("bad cert"))
        self.assertIn("--cacert", msg)


class RenderTests(unittest.TestCase):
    def test_respects_limit_and_shows_summary(self):
        r = sc.parse_response(SAMPLE, elapsed=0.25)
        out = sc.render(r, limit=1, style=sc.Style(False), width=80)
        self.assertIn(" 1. Linux kernel - Wikipedia", out)
        self.assertNotIn(" 2.", out)
        self.assertIn("engines: bing, duckduckgo", out)
        self.assertIn("Answer: 6.2137119224 mi", out)
        self.assertIn("1 of 3 results in 0.25s", out)

    def test_no_results(self):
        r = sc.parse_response({"query": "x", "results": []})
        self.assertIn("No results.", sc.render(r, 10, sc.Style(False), 80))


class MainTests(unittest.TestCase):
    def run_main(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {"QS_PASS": "secret"}, clear=False), \
             redirect_stdout(out), redirect_stderr(err):
            code = sc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_refuses_plain_http(self):
        code, _, err = self.run_main(["--url", "http://search.example", "x"])
        self.assertEqual(code, sc.EXIT_USAGE)
        self.assertIn("plain HTTP", err)

    def test_end_to_end_with_mocked_http(self):
        with mock.patch.object(sc.requests.Session, "get", return_value=fake_response(payload=SAMPLE)) as get:
            code, out, err = self.run_main(["--url", "https://search.example", "-c", "it", "-n", "2", "linux kernel"])
        self.assertEqual(code, sc.EXIT_OK)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["format"], "json")
        self.assertEqual(params["categories"], "it")
        self.assertIn("https://en.wikipedia.org/wiki/Linux_kernel", out)
        self.assertIn("engine unavailable: brave", err)

    def test_json_flag_prints_raw_payload(self):
        with mock.patch.object(sc.requests.Session, "get", return_value=fake_response(payload=SAMPLE)):
            code, out, _ = self.run_main(["--url", "https://search.example", "--json", "linux kernel"])
        self.assertEqual(code, sc.EXIT_OK)
        self.assertIn('"unresponsive_engines"', out)


if __name__ == "__main__":
    unittest.main()
