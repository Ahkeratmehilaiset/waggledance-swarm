"""Offline cases for the capture producer's B6 controlled-GraphQL PR source (``--pr-transport graphql``): its run
request budget (``HttpRequestBudget``: a slot taken before every send, never given back), its three reads
(``GraphqlPrSource.list``, ``row``, ``view``, each answered in the gh output's shape for the fields the producer
reads), its default transport (``https_post``: one direct HTTPS POST to the fixed origin, no proxy, netrc, redirect
or retry), its parse-time checks (``count_problem``), its selection (``pr_source``) and the producer's own
``pr_context``, ``fetch_view`` and ``fetch_row`` running over the selected source.

Every send goes to a strict fake transport that records it and answers from fixed bytes (or raises); the default
transport is exercised with a stand-in ``http.client.HTTPSConnection``. Nothing here sends a request, reads a real
credential or starts a process: the Authorization value is a fixed fake sentinel in a test-only variable. The
producer module is imported from its file (importing it runs nothing) with the code tree's modules set as the script
program sets them. These are feature cases (the GraphQL source is new), not a reproduction of a production failure;
gh's own internal traffic is not measured here.
"""

import ast
import importlib.util
import itertools
import json
import os
import ssl
import types
from pathlib import Path

import pytest

from tools import bridge_mind_workspace as ws
from tools import wd_bridge_mind as core
from waggledance.core.bridge_log_reader import parse_bridge_json_object

PRODUCER = Path(__file__).resolve().parent / "mind_capture_producer.py"
LOADED = itertools.count()
AUTH_ENV = "WD_B6_FAKE_AUTH_FOR_TESTS"
SENTINEL = "FAKE-B6-SENTINEL-not-a-credential"
HEAD, OTHER, MERGED = "a" * 40, "b" * 40, "c" * 40


def load():
    """A fresh producer module with the globals its script program sets (the code tree's modules, the repo)."""
    spec = importlib.util.spec_from_file_location(f"mind_capture_producer_b6_{next(LOADED)}", PRODUCER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ws, module.core, module.parse_bridge_json_object = ws, core, parse_bridge_json_object
    module.args = types.SimpleNamespace(repo="owner/repo")
    return module


class FakeHttp:
    """A strict fake transport: answers each send from ``answer(request)`` (a (status, body) pair, or an exception it
    raises) and records every send; a send to any other origin or path fails the case."""

    def __init__(self, answer):
        self.answer, self.sent = answer, []

    def __call__(self, host, path, body, headers, timeout, limit):
        assert (host, path) == ("api.github.com", "/graphql") and 0 < timeout and 0 < limit
        request = {"body": json.loads(body.decode("ascii")), "headers": dict(headers), "timeout": timeout,
                   "limit": limit}
        self.sent.append(request)
        result = self.answer(request)
        if isinstance(result, BaseException):
            raise result
        return result


def queue(*answers):
    """A FakeHttp answering the sends in order; a send past the queue fails the case."""
    pending = list(answers)
    return FakeHttp(lambda request: pending.pop(0))


def ok(data):
    return 200, json.dumps({"data": data}).encode("utf-8")


def node(number, state, head, branch):
    return {"number": number, "state": state, "headRefOid": head, "headRefName": branch}


def page(rows, cursor=None):
    return ok({"repository": {"pullRequests": {"pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor},
                                               "nodes": [node(*row) for row in rows]}}})


def row_answer(*row):
    return ok({"repository": {"pullRequest": node(*row)}})


def view_answer(head, *commits, total=None, author_totals=None):
    """A GraphQL view answer: one commit per name list, each name an author or co-author of that commit."""
    totals = author_totals or [len(names) for names in commits]
    nodes = [{"commit": {"authors": {"totalCount": count, "nodes": [{"name": name} for name in names]}}}
             for names, count in zip(commits, totals)]
    return ok({"repository": {"pullRequest": {"headRefOid": head, "commits": {
        "totalCount": len(commits) if total is None else total, "nodes": nodes}}}})


def graphql(module, transport, limit):
    return module.GraphqlPrSource("owner/repo", module.HttpRequestBudget(limit), lambda: "bearer " + SENTINEL,
                                  transport=transport)


def options(module, *extra):
    return module.parser.parse_args(["--src", "src", "--live-dir", "live", "--bin", "bin", *extra])


GRAPHQL_OPTIONS = ("--pr-transport", "graphql", "--http-request-budget", "10", "--graphql-auth-env", AUTH_ENV)


def test_the_budget_takes_a_slot_before_each_send_and_refuses_past_its_limit():
    module = load()
    for bad in (0, -1, True, 1.0, None, "5", module.HttpRequestBudget.maximum + 1):
        with pytest.raises(ValueError):
            module.HttpRequestBudget(bad)
    budget = module.HttpRequestBudget(2)
    assert [budget.take(), budget.take(), budget.take(), budget.take()] == [True, True, False, False]
    assert budget.sent == 2
    fake = queue(row_answer(7, "OPEN", HEAD, "lane-a/x"))
    source = graphql(module, fake, 1)
    assert source.row(7, 5) is not None and len(fake.sent) == 1 and source.budget.sent == 1
    assert source.row(7, 5) is None and source.reason == "http_budget_exhausted" and len(fake.sent) == 1
    assert source.view(7, 5) is None and source.list(5) is None and len(fake.sent) == 1


FAILED_SENDS = {
    "connection_error": (OSError("connection refused"), "graphql_send_failed"),
    "timeout": (TimeoutError("timed out"), "graphql_send_failed"),
    "redirect_moved": ((301, b""), "graphql_status"),
    "redirect_found_with_a_body": ((302, b'{"data": {}}'), "graphql_status"),
    "auth_refused": ((401, b'{"message": "Bad credentials"}'), "graphql_status"),
    "rate_limited": ((403, b'{"message": "rate limit"}'), "graphql_status"),
    "server_error": ((502, b""), "graphql_status"),
    "not_json": ((200, b"not json"), "graphql_unexpected"),
    "graphql_errors": ((200, json.dumps({"errors": [{"message": "x"}], "data": None}).encode()), "graphql_errors"),
    "partial_data_with_errors": ((200, json.dumps({"errors": [{"message": "x"}], "data": {
        "repository": {"pullRequest": node(7, "OPEN", HEAD, "lane-a/x")}}}).encode()), "graphql_errors"),
    "no_data_object": ((200, b'{"data": null}'), "graphql_unexpected"),
}


@pytest.mark.parametrize("case", FAILED_SENDS)
def test_a_failed_send_uses_its_slot_and_is_never_retried_followed_or_refreshed(case):
    answer, reason = FAILED_SENDS[case]
    module = load()
    fake = queue(answer, row_answer(7, "OPEN", HEAD, "lane-a/x"))  # the second answer is never asked for
    source = graphql(module, fake, 3)
    assert source.row(7, 5) is None and source.reason == reason
    assert len(fake.sent) == 1 and source.budget.sent == 1


def test_a_body_past_the_bound_is_unknown_and_the_transport_is_asked_for_one_byte_more_at_most():
    module = load()
    fake = queue((200, b" " * 65))
    source = graphql(module, fake, 2)
    source.max_response = 64
    assert source.row(7, 5) is None and source.reason == "graphql_response_too_large"
    assert fake.sent[0]["limit"] == 64 and len(fake.sent) == 1


def test_each_list_page_takes_a_slot_and_a_budget_short_of_the_pages_leaves_the_list_unknown():
    module = load()
    module.LIST_LIMIT = 5
    rows = [(n, "OPEN", HEAD, f"lane-a/x{n}") for n in range(1, 6)]
    pages = (page(rows[:2], "c1"), page(rows[2:4], "c2"), page(rows[4:], "c3"))
    fake = queue(*pages)
    source = graphql(module, fake, 2)
    source.page = 2
    assert source.list(30) is None and source.reason == "http_budget_exhausted" and len(fake.sent) == 2
    fake = queue(*pages)
    source = graphql(module, fake, 3)
    source.page = 2
    data = source.list(30)
    sent = [request["body"]["variables"] for request in fake.sent]
    assert [(v["after"], v["first"]) for v in sent] == [(None, 2), ("c1", 2), ("c2", 1)]
    assert {(v["owner"], v["name"]) for v in sent} == {("owner", "repo")} and source.budget.sent == 3
    assert module.pr_rows(data) == ({str(n): ("OPEN", HEAD, f"lane-a/x{n}") for n in range(1, 6)}, 0)
    # a list that reaches LIST_LIMIT stops there, with no page past it (the producer then reads it as truncated)
    assert json.loads(data) == [node(*row) for row in rows]


LIST_REFUSALS = {
    "cursor_with_a_space": (page([(1, "OPEN", HEAD, "lane-a/x1")] * 2, "bad cursor"), "graphql_cursor_unexpected"),
    "cursor_too_long": (page([(1, "OPEN", HEAD, "lane-a/x1")] * 2, "c" * 257), "graphql_cursor_unexpected"),
    "cursor_not_a_string": (page([(1, "OPEN", HEAD, "lane-a/x1")] * 2, 7), "graphql_cursor_unexpected"),
    "short_page_with_a_next_page": (page([(1, "OPEN", HEAD, "lane-a/x1")], "c1"), "graphql_list_unexpected"),
    "nodes_not_a_list": (ok({"repository": {"pullRequests": {"pageInfo": {"hasNextPage": False}, "nodes": {}}}}),
                         "graphql_list_unexpected"),
    "no_repository": (ok({"repository": None}), "graphql_list_unexpected"),
}


@pytest.mark.parametrize("case", LIST_REFUSALS)
def test_a_list_page_of_another_shape_or_with_an_unbounded_cursor_is_unknown_without_a_further_send(case):
    answer, reason = LIST_REFUSALS[case]
    module = load()
    fake = queue(answer)
    source = graphql(module, fake, 5)
    source.page = 2
    assert source.list(30) is None and source.reason == reason and len(fake.sent) == 1


def test_the_list_time_bound_refuses_before_a_send(monkeypatch):
    module = load()
    fake = queue(page([(1, "OPEN", HEAD, "lane-a/x1")]))
    source = graphql(module, fake, 5)
    assert source.list(0) is None and source.reason == "graphql_list_timeout" and fake.sent == []
    assert source.budget.sent == 0


class FakeConnection:
    """A stand-in http.client.HTTPSConnection: records its construction and calls; a tunnel fails the case."""

    made = []

    def __init__(self, host, port=None, timeout=None, context=None, **extra):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.extra, self.calls = extra, []
        FakeConnection.made.append(self)

    def set_tunnel(self, *args, **kwargs):
        raise AssertionError("a proxy tunnel was set")

    def request(self, method, path, body=None, headers=None):
        self.calls.append(("request", method, path, body, dict(headers)))

    def getresponse(self):
        self.calls.append(("getresponse",))

        def read(amount):
            self.calls.append(("read", amount))
            return b'{"message": "Moved"}'
        return types.SimpleNamespace(status=301, read=read, getheader=lambda name, default=None: "https://elsewhere")

    def close(self):
        self.calls.append(("close",))


def test_the_default_transport_is_one_direct_https_post_without_proxy_netrc_redirect_or_retry(monkeypatch):
    module = load()
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "HTTP_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.setenv("NETRC", str(Path(__file__).resolve().parent / "no-such-netrc"))
    FakeConnection.made = []
    monkeypatch.setattr(module.http.client, "HTTPSConnection", FakeConnection)
    source = graphql(module, None, 3)
    assert source.transport is module.https_post
    assert source.row(7, 5) is None and source.reason == "graphql_status" and source.budget.sent == 1
    [connection] = FakeConnection.made  # one connection, no second one for the redirect
    assert (connection.host, connection.port, connection.timeout, connection.extra) == ("api.github.com", 443, 5, {})
    assert isinstance(connection.context, ssl.SSLContext) and connection.context.check_hostname
    assert connection.context.verify_mode == ssl.CERT_REQUIRED
    (request, method, path, body, headers), *rest = connection.calls
    assert (request, method, path) == ("request", "POST", "/graphql")
    assert rest == [("getresponse",), ("read", source.max_response + 1), ("close",)]
    assert headers["Authorization"] == "bearer " + SENTINEL and set(headers) == {
        "Authorization", "Content-Type", "Accept", "User-Agent"}
    assert set(json.loads(body.decode("ascii"))) == {"query", "variables"}


def test_the_queries_ask_only_the_public_capture_fields():
    module = load()
    fields = {"LIST": "pageInfo{hasNextPage endCursor}nodes{number state headRefOid headRefName}",
              "ROW": "pullRequest(number:$number){number state headRefOid headRefName}",
              "VIEW": "headRefOid commits(first:100){totalCount nodes{commit{authors(first:100){totalCount "
                      "nodes{name}}}}}"}
    for name, needle in fields.items():
        query = getattr(module.GraphqlPrSource, name)
        assert needle in query, name
        for private in ("email", "login", "body", "message", "title", "files", "author{", "user"):
            assert private not in query.replace("authors(", "").replace("headRefName", ""), (name, private)


@pytest.mark.parametrize("extra, reason", [
    (("--pr-transport", "graphql", "--graphql-auth-env", AUTH_ENV), "budget"),
    (("--pr-transport", "graphql", "--http-request-budget", "0", "--graphql-auth-env", AUTH_ENV), "budget"),
    (("--pr-transport", "graphql", "--http-request-budget", "-3", "--graphql-auth-env", AUTH_ENV), "budget"),
    (("--pr-transport", "graphql", "--http-request-budget", "1000001", "--graphql-auth-env", AUTH_ENV), "budget"),
    (("--pr-transport", "graphql", "--http-request-budget", "5"), "auth"),
    (("--pr-transport", "graphql", "--http-request-budget", "5", "--graphql-auth-env", "WD_B6_UNSET_FOR_TESTS"),
     "auth"),
    (("--pr-transport", "graphql", "--http-request-budget", "5", "--graphql-auth-env", "lower case"), "auth"),
    (("--pr-transport", "graphql", "--http-request-budget", "5", "--graphql-auth-env", AUTH_ENV,
      "--repo", "a/b/c"), "repo"),
    (("--http-request-budget", "5"), "gh"),
    (("--graphql-auth-env", AUTH_ENV), "gh"),
])
def test_the_graphql_mode_needs_an_explicit_positive_budget_and_an_auth_variable_and_gh_takes_neither(
        monkeypatch, extra, reason):
    module = load()
    monkeypatch.setenv(AUTH_ENV, SENTINEL)
    monkeypatch.delenv("WD_B6_UNSET_FOR_TESTS", raising=False)
    expected = {"budget": f"--pr-transport graphql needs --http-request-budget 1..{module.HttpRequestBudget.maximum}",
                "auth": "--pr-transport graphql needs --graphql-auth-env naming a set variable",
                "repo": "--pr-transport graphql needs --repo owner/name",
                "gh": "--http-request-budget and --graphql-auth-env apply only to --pr-transport graphql"}[reason]
    assert module.count_problem(options(module, *extra)) == expected


@pytest.mark.parametrize("value", ["", "two words", "line\nbreak", "tab\tvalue", "non-ascii-" + chr(228), "x" * 1025])
def test_an_auth_variable_of_another_shape_is_refused_at_parse_time(monkeypatch, value):
    module = load()
    monkeypatch.setenv(AUTH_ENV, value)
    assert module.count_problem(options(module, *GRAPHQL_OPTIONS)) == (
        "--pr-transport graphql needs --graphql-auth-env naming a set variable")


def test_the_default_stays_the_gh_path_with_its_own_list_call(tmp_path):
    module = load()
    parsed = options(module)
    assert (parsed.pr_transport, parsed.http_request_budget, parsed.graphql_auth_env) == ("gh", None, None)
    assert module.count_problem(parsed) is None and module.pr_source(parsed, {}) is None
    assert module.GraphqlPrSource.selected is None
    calls = []
    module.gh = lambda timeout, *argv: calls.append((timeout, argv))
    context, record = module.pr_context(tmp_path)
    assert context is None and (record["status"], record["reason"]) == ("UNKNOWN", "gh_list_unavailable")
    assert calls == [(module.GH_TIMEOUT, ("pr", "list", "--repo", "owner/repo", "--state", "all", "--limit", "400",
                                          "--json", "number,state,headRefOid,headRefName"))]


def route(views, rows):
    """A FakeHttp answering list pages, rows and views of a small repo by the query each send names."""

    def answer(request):
        query, variables = request["body"]["query"], request["body"]["variables"]
        if "pullRequests(" in query:
            start = 0 if variables["after"] is None else int(variables["after"][1:])
            end = start + variables["first"]
            return page(rows[start:end], f"i{end}" if end < len(rows) else None)
        if "commits(" in query:
            return views[variables["number"]]
        return row_answer(*[row for row in rows if row[0] == variables["number"]][0])
    return FakeHttp(answer)


ROWS = [(7, "OPEN", HEAD, "lane-a/x7"), (8, "OPEN", OTHER, "lane-b/y8"), (9, "MERGED", MERGED, "lane-c/z9")]
VIEWS = {7: view_answer(HEAD, ["lane-a"], ["lane-a", "lane-b"]),  # lane-b only as a co-author
         8: view_answer(HEAD, ["lane-b"])}  # a view of another head than the list's


def test_the_selected_graphql_source_runs_the_producers_own_pr_context_with_the_gh_paths_output(
        monkeypatch, tmp_path):
    module = load()
    monkeypatch.setenv(AUTH_ENV, SENTINEL)
    parsed = options(module, *GRAPHQL_OPTIONS)
    assert module.count_problem(parsed) is None
    fake = route(VIEWS, ROWS)
    source = module.pr_source(parsed, os.environ, transport=fake)
    assert AUTH_ENV not in os.environ  # taken once, so no process this producer starts inherits it
    assert isinstance(source, module.GraphqlPrSource) and source.budget.limit == 10
    module.GraphqlPrSource.selected = source
    cache = tmp_path / "files-cache"
    cache.mkdir()
    context, record = module.pr_context(cache, needed={7, 8, 9})
    assert record["status"] == "observed" and (record["views_fetched"], record["views_unknown"]) == (2, 1)
    assert context["prs"]["7"]["commit_authors"] == ["lane-a", "lane-b"]
    assert context["prs"]["8"]["commit_authors"] is None and context["prs"]["9"]["commit_authors"] is None
    assert [path.name for path in cache.iterdir()] == [f"7-{HEAD}.view.v1.json"]
    assert len(fake.sent) == 3 and source.budget.sent == 3
    assert {request["headers"]["Authorization"] for request in fake.sent} == {"bearer " + SENTINEL}
    written = b"".join(path.read_bytes() for path in cache.iterdir())
    for text in (json.dumps(context), json.dumps(record), written.decode("ascii"), repr(vars(source))):
        assert SENTINEL not in text
    # the gh path over the same output bytes builds the same context and record (observed time and ms aside)
    same = graphql(load(), route(VIEWS, ROWS), 10)
    answers = {("pr", "list"): same.list(60), ("pr", "view", "7"): same.view(7, 60),
               ("pr", "view", "8"): same.view(8, 60)}
    other = load()
    other.gh = lambda timeout, *argv: answers[argv[:2] if argv[1] == "list" else argv[:3]]
    cache_gh = tmp_path / "files-cache-gh"
    cache_gh.mkdir()
    context_gh, record_gh = other.pr_context(cache_gh, needed={7, 8, 9})
    assert dict(context, observed_utc=None) == dict(context_gh, observed_utc=None)
    assert dict(record, observed_utc=None, ms=None) == dict(record_gh, observed_utc=None, ms=None)
    assert (cache / f"7-{HEAD}.view.v1.json").read_bytes() == (cache_gh / f"7-{HEAD}.view.v1.json").read_bytes()


def test_a_spent_run_budget_leaves_every_later_context_unknown_with_its_fixed_reason(tmp_path):
    module = load()
    fake = route(VIEWS, ROWS)
    module.GraphqlPrSource.selected = graphql(module, fake, 3)
    assert module.pr_context(tmp_path)[1]["status"] == "observed" and len(fake.sent) == 3
    for _ in range(2):
        context, record = module.pr_context(tmp_path)
        assert context is None and (record["status"], record["reason"]) == ("UNKNOWN", "http_budget_exhausted")
    assert len(fake.sent) == 3


def test_a_list_at_its_limit_reads_each_missing_snapshot_pr_with_one_budgeted_row_send(tmp_path):
    module = load()
    module.LIST_LIMIT = 2
    fake = route({7: VIEWS[7], 8: view_answer(OTHER, ["lane-b"])}, ROWS)
    module.GraphqlPrSource.selected = graphql(module, fake, 10)
    context, record = module.pr_context(tmp_path, needed={7, 8, 9})
    assert record["status"] == "observed" and record["rows_added"] == 1 and sorted(context["prs"]) == ["7", "8", "9"]
    kinds = ["list" if "pullRequests(" in r["body"]["query"] else "view" if "commits(" in r["body"]["query"]
             else "row" for r in fake.sent]
    assert kinds == ["list", "row", "view", "view"]


VIEW_CASES = {
    "authors_and_coauthors": (view_answer(HEAD, ["lane-a"], ["lane-b", "lane-c", "Some Person"]),
                              ["lane-a", "lane-b", "lane-c"]),
    "another_head": (view_answer(OTHER, ["lane-a"]), "no view"),
    "a_hundred_commits": (view_answer(HEAD, *[["lane-a"]] * 100), None),
    "more_commits_than_listed": (view_answer(HEAD, *[["lane-a"]] * 3, total=150), None),
    "more_authors_than_listed": (view_answer(HEAD, ["lane-a", "lane-b"], author_totals=[3]), None),
    "no_commits": (view_answer(HEAD), None),
    "pr_missing": (ok({"repository": {"pullRequest": None}}), "no view"),
    "commits_of_another_shape": (ok({"repository": {"pullRequest": {"headRefOid": HEAD, "commits": []}}}), "no view"),
}


@pytest.mark.parametrize("case", VIEW_CASES)
def test_a_view_keeps_every_author_and_coauthor_name_and_stays_unknown_when_incomplete_or_on_another_head(case):
    answer, expected = VIEW_CASES[case]
    module = load()
    fake = queue(answer)
    module.GraphqlPrSource.selected = graphql(module, fake, 2)
    view = module.fetch_view(7, HEAD, 5)
    assert len(fake.sent) == 1 and fake.sent[0]["body"]["variables"]["number"] == 7
    if expected == "no view":
        assert view is None
    else:
        assert view == module.view_object(7, HEAD, expected)


def test_fetch_row_over_the_graphql_source_keeps_the_list_row_shape():
    module = load()
    fake = queue(row_answer(9, "MERGED", MERGED, "lane-c/z9"), row_answer(10, "OPEN", HEAD, "lane-c/z9"))
    module.GraphqlPrSource.selected = graphql(module, fake, 3)
    row, data = module.fetch_row(9, 5)
    assert row == ("MERGED", MERGED, "lane-c/z9") and json.loads(data) == node(9, "MERGED", MERGED, "lane-c/z9")
    assert module.fetch_row(9, 5) is None  # an answer naming another number


def test_the_script_selects_the_pr_source_once_after_the_start_reads_and_before_the_first_cycle():
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    [main] = [node for node in tree.body
              if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"]
    texts = [ast.unparse(statement) for statement in main.body]
    [selection] = [i for i, text in enumerate(texts) if "pr_source(args, os.environ)" in text]
    [loop] = [i for i, statement in enumerate(main.body) if isinstance(statement, ast.For)]
    [reads] = [i for i, text in enumerate(texts) if "read_seed(" in text]
    assert reads < selection < loop
    assert "GraphqlPrSource.selected = pr_source(args, os.environ)" in texts[selection]
    assigned = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and ast.unparse(node.targets[0]) == "GraphqlPrSource.selected"]
    assert len(assigned) == 1
