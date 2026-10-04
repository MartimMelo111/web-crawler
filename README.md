# webcrawler

A fast, single-domain web crawler for the command line. Give it a base URL and it visits
every page reachable on that hostname. For each page it prints the page's URL and every URL
found on it.

```
$ webcrawler https://books.toscrape.com -n 300
https://books.toscrape.com/
  -> https://books.toscrape.com/index.html
  -> https://books.toscrape.com/catalogue/category/books_1/index.html
  -> https://books.toscrape.com/catalogue/category/books/travel_2/index.html
  ...
crawled 300 pages, 0 redirects, 0 skipped, 0 errors in 2.45s
```

## Quick start

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```
uv sync                                      # create .venv and install locked dependencies
uv run webcrawler https://example.com        # crawl a site
uv run python -m webcrawler https://example.com   # same thing, as a module
```

Quote URLs that contain shell metacharacters such as `(`, `)`, `&` or `?`. PowerShell, for
example, would otherwise try to run `(gamer)` as a command:

```
uv run webcrawler 'https://en.wikipedia.org/wiki/Faker_(gamer)' -n 50
```

### Options

| Option | Default | Description |
|---|---|---|
| `url` | (required) | Base URL to start from |
| `-c, --concurrency` | 20 | Maximum number of requests in flight at once |
| `-n, --max-pages` | no limit | Stop after fetching this many URLs |
| `-t, --timeout` | 10 | Per-request timeout in seconds |
| `-r, --retries` | 2 | Retries for network errors and 429/5xx responses |
| `-f, --format` | `text` | `text` (readable) or `jsonl` (one JSON object per page) |
| `--user-agent` | `webcrawler/0.1 (...)` | User-Agent header to send |
| `--ignore-robots` | off | Don't honour `robots.txt` |
| `--no-http2` | off | Use HTTP/1.1 only |
| `-v, --verbose` | off | `-v` for info logs, `-vv` for debug logs (written to stderr) |

Results go to **stdout** and logs and the final summary go to **stderr**, so the output can be
piped straight into other tools:

```
uv run webcrawler https://example.com -f jsonl | jq -r '.links[]' | sort -u
```

Every crawl also **saves its results to a file**, while the terminal shows them as usual. Each
run gets its own file in a `results/` folder (created when needed, and ignored by git), named
after the start URL plus the date and time, so earlier results are never overwritten:

```
https://www.example.com/           →  results/www.example.com_2026-10-03_14-20-05.jsonl
https://www.example.com/docs/api   →  results/www.example.com_docs_api_2026-10-03_14-20-05.jsonl
```

In the name, the scheme is dropped, characters that aren't allowed in file names become `_`,
and very long URLs are shortened to 100 characters.

The file uses JSON Lines (one JSON object per page, one per line), the same format as
`-f jsonl`. Each page is written as soon as it is crawled, so if the crawl is interrupted the
file still holds every page found so far, and every line in it is valid JSON. A single JSON
array would be unreadable if the crawl stopped before its closing `]`. If the file can't be
created, that is reported before the crawl starts.

The exit code is `0` on success, `1` if no page could be crawled at all, `2` for invalid
arguments, and `130` on Ctrl-C.

## Development

```
uv run pytest                 # run the test suite
uv run pytest --cov           # with branch coverage
uv run ruff format .          # format
uv run ruff check .           # lint
uv run mypy                   # type-check (strict mode, src and tests)
```

CI and the pre-commit hooks run the same checks; see [Production setup](#production-setup).

### Project layout

Each test file sits next to the module it tests.

```
src/webcrawler/
  cli.py                 argument parsing; connects the parts below
  cli_test.py              CLI wiring and argument validation
  integration_test.py      the real CLI against a real HTTP server on localhost
  conftest.py            the `site` pytest fixture
  testing/               test support code (not shipped)
    builders.py          random test data: hosts, paths, URLs, user agents, bytes
    mock_site.py         MockSite: an in-memory website on a random host (httpx.MockTransport)
  core/
    crawler.py           crawl engine: URL queue, worker pool, de-duplication, scope
    crawler_test.py        dedup, scope, redirects, concurrency, limits
  services/              components that talk to the network
    fetcher.py           HTTP: shared client, streaming, size limits, retries with backoff
    fetcher_test.py        retries, backoff, Retry-After, redirects, body limits
    robots.py            robots.txt policy
    robots_test.py         robots.txt status handling and rules
  tools/                 pure helpers with no I/O
    urls.py              URL normalisation and the same-domain scope rule
    urls_test.py           normalisation and scope rules (table-driven)
    parser.py            link extraction from HTML
    parser_test.py         link extraction edge cases
  output/
    reporters.py         text and JSON Lines reporters, and one that sends to several at once
    reporters_test.py      output formats, Unicode, format registry
```

Dependencies only point one way: `cli` → `core` → `services` → `tools`. `tools` has no
dependencies inside the package and does no I/O, so it is the easiest layer to test and reuse.

Keeping tests next to the code makes it obvious which code has no tests, and a module and its
tests get moved or renamed together. `*_test.py`, `conftest.py` and `testing/` are left out
of the built wheel (`[tool.uv.build-backend] wheel-exclude`), so installing the package doesn't
install the tests.

### Production setup

The repo is set up the way I would set up a project a team maintains, not a one-off script:

- **Reproducible environments.** `uv` manages Python and dependencies. `uv.lock` pins every
  package version, and `.python-version` pins the interpreter, so a reviewer, CI and I all run
  the same thing. CI installs with `uv sync --locked`, which fails if the lockfile is out of date.
- **One config file.** `pyproject.toml` holds the package metadata, dependencies, the build
  backend and the settings for pytest, coverage, ruff and mypy.
- **An installable package.** The code lives under `src/` (the src layout), so tests run against
  the installed package and packaging mistakes show up. `[project.scripts]` provides the
  `webcrawler` command, and `py.typed` tells type checkers in other projects that the package
  ships type hints.
- **Runtime and development dependencies are separate.** Only `httpx` and `selectolax` are
  installed for users. pytest, ruff, mypy and the rest are in a `dev` dependency group.
- **Quality gates.**
  - `ruff` formats the code and lints it with bug-catching rule sets.
  - `mypy --strict` type-checks every file.
  - CI fails if branch coverage drops below 100%.
  - `pytest-randomly` shuffles test order and seeds random test data, which catches tests that
    depend on each other.
- **CI** (`.github/workflows/ci.yml`) runs on every push to `main` and every pull request.
  - The lint job and the test job run separately, so a formatting problem and a failing test
    show up as separate failures.
  - Tests run on Linux, Windows and macOS.
  - A final `ci-passed` job succeeds only if every other job did, so branch protection needs
    just that one required check, however the test matrix changes.
  - A new push to a PR cancels the run for the previous commit.
  - The workflow only has read access to the repo.
- **Pre-commit hooks** (`.pre-commit-config.yaml`) run the same format, lint and type checks
  before each commit, so most problems are caught before CI. Install them with
  `uv run --with pre-commit pre-commit install`.

---

## Development environment

### IDE: PyCharm

I wrote and tested the project in **PyCharm 2026.2.3** (build 262.10968.92) on Windows 11 Pro.

- **Project setup.** PyCharm loads the project straight from `pyproject.toml` and marks `src/`
  as the source root, so imports such as `from webcrawler.core.crawler import Crawler` resolve
  in the editor the same way they do when the package is installed.
- **Interpreter.** The project interpreter is the project's own `.venv`, which `uv sync`
  creates with Python 3.14. PyCharm, the terminal and CI therefore all use the same Python
  version and the same locked dependency versions from `uv.lock`.
- **Running tests.** I run tests with PyCharm's pytest runner. Because each test file sits next
  to its module and groups its tests into classes, I can run the whole suite, one file, one
  class (for example `TestRedirects`) or a single test from the gutter icons, and see the
  results as a tree. The settings it uses (`testpaths`, `python_files = ["*_test.py"]`,
  `asyncio_mode = "auto"`) come from `pyproject.toml`, so PyCharm and the command line run the
  tests the same way.
- **Type hints.** The code is fully annotated and checked with `mypy --strict`, so PyCharm's own
  inspections and autocompletion have complete type information to work with.
- **Terminal.** PyCharm's built-in terminal runs Git Bash
  (`"C:\Program Files\Git\bin\bash.exe" --login -i`) with
  [Oh My Bash](https://github.com/ohmybash/oh-my-bash), which shows the current git branch in
  the prompt, and [ble.sh](https://github.com/akinomyoga/ble.sh), which suggests commands from
  history as I type. The project's virtual environment is activated automatically in each new
  terminal tab.
- **Checks outside the IDE.** Formatting, linting and type checking don't depend on PyCharm:
  they run as `uv run` commands, through the pre-commit hooks, and in CI. Anyone can work on the
  project with a different editor and get exactly the same results.

### Getting familiar with the subject

I came to the project already comfortable with `asyncio`, so the concurrency model was familiar
ground. The following were new to me, and I learned them during the project from the official
documentation and by asking Claude questions:

- **`httpx.AsyncClient`**: how one shared client pools and reuses connections, streams
  responses, handles timeouts and enables HTTP/2, and how `MockTransport` can stand in for the
  network in tests.
- **The HTTP behaviour a fast crawler relies on**: connection pooling, HTTP/2 multiplexing,
  streaming responses so non-HTML bodies are never downloaded, and why `Content-Length` alone
  can't be trusted to limit a body's size.
- **`robots.txt`**: I didn't know about the convention before this project. Claude pointed out
  that a well-behaved crawler should respect it, and from there I learned how its rules are
  matched with `urllib.robotparser`, and why the status of the robots.txt response itself
  matters.
- **selectolax's `LexborHTMLParser`**: how to parse a page with it, which elements count as
  links to other pages, how `<base href>` changes what relative links resolve against, and how
  it handles malformed HTML.
- **URL normalisation**: which rewrites are safe for de-duplication and which, such as changing
  the query string, can merge pages that are actually different.

The reasoning behind each of these choices is in the [Design discussion](#design-discussion).

### AI assistance

I used Claude, through Claude Code, as an assistant while building this project. The work it
did was in response to specific instructions from me, and I reviewed everything it produced
before keeping it. Claude was most valuable on topics I was less familiar with, where its
explanations helped me a lot. How much it contributed varied by part of the project:

- **Crawl engine and HTTP layer: mostly mine.** I designed and wrote the crawl engine
  (`core/crawler.py`) and the fetcher (`services/fetcher.py`), and made the design decisions
  for them described below. Along the way I asked Claude questions to check ideas, compare options and
  understand library behaviour.
- **HTML parser: a lot of help.** `LexborHTMLParser` was new to me, and Claude helped a lot with
  link extraction in `tools/parser.py`.
- **URL rules: some help.** Claude helped me work out which parts of a URL are safe to
  normalise and which must be left alone (such as the query string), which shaped the
  normalisation and scope rules in `tools/urls.py`.
- **Reporters: mostly AI.** Output formatting wasn't where I focused my time, so AI wrote most of
  `output/reporters.py`, following my instructions on the formats.
- **`robots.txt` support: AI-led.** Claude warned me that the crawler should respect
  `robots.txt`, which I hadn't known about, and implemented that support
  (`services/robots.py`).
- **Project setup.** I'm already familiar with setting up Python projects this way, so here AI
  was used only to save time: it set up the project environment (`uv`, `pyproject.toml`, ruff,
  mypy, pre-commit and CI) so I could get to the actual crawler sooner.
- **Refactoring as the code grew.** As the project progressed and got more complex, AI helped
  refactor the code to follow my own code standards, such as splitting it into separate
  directories for `core`, `services`, `tools` and `output`.
- **Tests.** AI assisted with writing the test suite, and helped refactor the tests to use the
  random data builders in `testing/builders.py`, which sped up writing new tests. I used AI to
  find and cover the remaining untested branches to reach 100% branch coverage.

---

## Design discussion

### The problem

Crawling is mostly **waiting on the network**. A page takes tens to hundreds of milliseconds
to arrive and around a millisecond to parse. So speed comes from keeping many requests in
flight at once and making each one as cheap as possible. Raw CPU speed matters much less.
Accuracy means visiting every reachable page **exactly once**, staying strictly on the one
hostname, and reporting links the way a browser would resolve them.

### Concurrency model: asyncio with a fixed worker pool

Options I considered:

| Approach | Pros | Cons |
|---|---|---|
| Sequential `requests` | Simplest | One request at a time |
| Thread pool + `requests` | Familiar; parallel I/O | One OS thread per in-flight request; shared state needs locks; harder to cancel cleanly |
| `multiprocessing` | True CPU parallelism | The work isn't CPU-bound; pickling and IPC overhead; much heavier |
| **asyncio + `httpx`** | Thousands of in-flight requests in one thread; no locks; clean cancellation | Everything in the call path must be async |

I chose **asyncio**. `Crawler` starts `concurrency` worker tasks that pull URLs from a shared
`asyncio.Queue`. Key points:

- **A bounded worker pool instead of one task per URL.** Creating a task per discovered link
  would let memory and open sockets grow without limit on a large site. A fixed pool caps both,
  and `concurrency` becomes the single setting for how hard we push the server.
- **Mark URLs as seen when they are enqueued, not when they are fetched.** If ten pages link to
  `/about` at the same moment, only the first enqueue counts. Every URL is fetched at most once
  with no race condition. Shared state needs no locks because the event loop only switches tasks
  at `await` points, and `_enqueue` never awaits.
- **Clean termination.** `queue.join()` finishes when every enqueued URL has been processed.
  Then the workers are cancelled. Each worker catches unexpected exceptions per URL, so a bug
  triggered by one strange page is logged and counted instead of killing a worker. There is a
  test for exactly that.
- **`asyncio.TaskGroup` over `asyncio.gather`.** The workers run inside a `TaskGroup`, so no
  task can outlive `run()`: the group only exits once every worker has stopped. I started with
  `gather(..., return_exceptions=True)` after `join()`, which works but has two weaknesses.
  A worker that died mid-crawl would go unnoticed until the end, so `join()` could hang waiting
  for URLs nobody was taking. And on Ctrl-C the cleanup had to be written by hand in a
  `finally`. With a `TaskGroup`, a failing worker cancels the others and the error is raised
  straight away, and cancelling `run()` cancels and waits for every worker automatically. The
  cost is that failures surface as an `ExceptionGroup` rather than a bare exception.
- **Streaming output.** Results are printed as each page finishes, not saved up until the end.
  Memory stays flat and the user sees progress straight away. Each page is written with a single
  `write()` call so output from different workers never interleaves.

### HTTP layer: `httpx` with one shared client

- **Connection reuse.** One `AsyncClient` means one connection pool, so the TCP and TLS
  handshake is paid once per connection, not once per page. For a single host this is one of the
  biggest wins available.
- **HTTP/2** (on by default, `--no-http2` to turn off) multiplexes many requests over one
  connection where the server supports it.
- **Pool size equals `concurrency`**, so workers never queue behind each other waiting for a
  connection.
- I picked `httpx` over `aiohttp` because it supports HTTP/2, has a cleaner typed API, and
  ships `MockTransport`, which made the test suite fast and free of network calls. `aiohttp`
  is slightly faster in raw benchmarks, but the network round trip dominates here.

**Not wasting resources:**

- Responses are **streamed**. If the `Content-Type` isn't HTML (PDFs, images, zips), the
  connection is released as soon as the headers arrive and the body is never downloaded.
- HTML bodies are capped at 10 MB. The cap is checked against `Content-Length` first and then
  while streaming, so a server that lies or sends chunked data can't exhaust memory.
- **Retries** happen only for errors that might be temporary: connection or read failures,
  timeouts, and 429/500/502/503/504. Delays use exponential backoff with jitter so workers don't
  retry in lockstep. A `Retry-After` header is respected, up to a limit. 4xx errors are never
  retried. The sleep function is injected, so retry tests run instantly.
- `robots.txt` is honoured by default (`--ignore-robots` to opt out). If robots.txt can't be
  fetched, the crawler treats the site as unrestricted so a flaky file doesn't block the crawl.
  A 401/403 on robots.txt means everything is disallowed, with a warning on stderr. That
  follows `urllib.robotparser` and is stricter than RFC 9309, which allows crawling on any 4xx.
  In practice a 403 there usually means bot protection (e.g. Cloudflare) that would block every
  page anyway, so it's better to stop straight away and explain why. Like Google, only the
  first 500 KB of robots.txt is read, so a huge file can't use up memory before the crawl starts.
- Responses that are broken in a way a retry won't fix, such as a corrupt compressed body, are
  reported as ordinary errors and not retried.

### Parsing: `selectolax` (Lexbor)

I compared BeautifulSoup (with `html.parser` or `lxml`), `lxml` directly, and `selectolax`.
`selectolax` uses the Lexbor engine, an HTML5-compliant C parser. For this job it is many times
faster than BeautifulSoup, and it copes with malformed markup the way browsers do. Parsing
runs on the event loop thread. I measured about 1 ms per page, including URL normalisation, on
a 50 KB page. Network round trips are 50–100× longer than that, so moving parsing to a thread
pool would add overhead with no real benefit. If profiling ever showed parsing as the bottleneck, wrapping it in
`asyncio.to_thread` would be a one-line change.

**Which links count:** `<a href>` and `<area href>`, which are the elements that point to other
documents. `<img>`, `<script>` and `<link rel=stylesheet>` are resources, not pages, so they are
left out. Relative links are resolved against `<base href>` when the page has one, as browsers
do. `mailto:`, `javascript:`, `tel:` and other non-HTTP links are dropped.

### URL normalisation and scope

Correct de-duplication depends on recognising that two spellings point to the same page.
`normalize_url` only applies rewrites that the URL spec guarantees are safe: lower-case scheme
and host, drop the default port, drop the fragment, `""` → `/`, resolve `.` and `..` path
segments (`/a/../b` → `/b`), strip a trailing dot from the host, and remove user info. It leaves the **query string unchanged**. Sorting parameters or
removing "tracking" ones like `utm_*` would remove more duplicates, but it can merge URLs the
server treats differently, and that would be an accuracy bug.

**Scope** is an exact hostname match, as the brief asks: `example.com` does **not** include
`www.example.com` or `blog.example.com`. Scheme and port are ignored, so `http://` and
`https://` pages on the same host are both crawled. That seemed closer to what "the same
domain" means than treating them as two sites. Off-site links are still *printed*, because the
brief asks for all URLs on each page. They just aren't *followed*.

**Redirects** are handled by the crawler, not by httpx. A 3xx is reported as a page whose only
link is the target, and the target goes through the usual scope and de-duplication checks. This
stops a redirect from carrying the crawler off-domain, and means a page reached through several
redirects is still fetched only once.

### Testing strategy

The suite reaches 100% branch coverage with no `pragma: no cover` exclusions, and runs in about
a second:

- **Grouped by logic.** Each test file has one test class per connected piece of behaviour
  (`TestRedirects`, `TestRetries`, `TestLimits`, ...). A class runs that whole area at once,
  and each method is still a single test that can be run on its own:

  ```
  uv run pytest src/webcrawler/core/crawler_test.py                          # whole file
  uv run pytest src/webcrawler/core/crawler_test.py::TestRedirects           # one area
  uv run pytest "src/webcrawler/core/crawler_test.py::TestRedirects::test_off_site_redirect_is_not_followed"
  uv run pytest -k TestRetries                                               # by name
  ```

- **Unit tests** cover each pure function with table-driven cases (`pytest.mark.parametrize`),
  aimed at the edge cases that actually break crawlers: odd ports, IPv6, protocol-relative
  links, `<base>`, malformed HTML, look-alike domains (`<host>.<other-host>`).
- **Random test data.** Hosts, paths, page contents, limits and counts come from
  `testing/builders.py` instead of hand-picked literals, so a test can only pass by checking
  the relationship it is about, never by a coincidence in a fixed value. Values that *are* the
  behaviour under test (status codes, URL schemes, content types, the output format) stay
  literal. `pytest-randomly` seeds the randomness, prints the seed at the top of every run
  (`Using --randomly-seed=N`), and shuffles test order to catch tests that depend on each
  other. Any failure can be replayed with `uv run pytest -p randomly --randomly-seed=N`.
- **Behaviour tests** use `MockSite`, an in-memory site served through `httpx.MockTransport`.
  It records every request and the **peak number of requests in flight**, so the tests can check
  properties rather than output: each page fetched exactly once, concurrency never above the
  limit, no request to a disallowed or off-site URL.
- **One end-to-end test** runs the real `main()` against a real `ThreadingHTTPServer` on
  localhost, covering real sockets, a real 301 (directory without trailing slash), a 404, and a
  PDF that gets skipped.
- **Static checks:** `mypy --strict` over source and tests, and `ruff` with bug-catching rule
  sets (`B`, `ASYNC`, `SIM`, `RUF`, `PT`).

On top of the automated tests, I checked by hand that output looks right.

### Trade-offs I made deliberately

- **No per-host politeness delay.** The brief puts speed first, and `concurrency` already limits
  load. A production crawler aimed at sites I don't own would add a token-bucket rate limit
  (see below).
- **Breadth-first-ish order, not strict BFS.** With concurrent workers the output order is not
  fixed. The *set* of pages and links is. JSONL output with sorting downstream handles cases
  where order matters.
- **No depth limit; `--max-pages` is the only bound.** Sites that generate endless distinct
  URLs (calendars with a "next month" link, faceted search) are crawler traps: without
  `-n` the crawl would never end on them.
- **In-memory queue and seen-set.** Fine for millions of URLs (roughly 100 bytes each). It
  would need to change for web-scale crawls.
- **No JavaScript rendering.** Links added by client-side JS are missed. Covering them needs a
  headless browser, which the brief rules out and which costs about 100× more per page.

### Future improvements

- **`--max-depth`**, the standard defence against crawler traps. It is less simple than it looks
  with concurrent workers: depth must be the *shortest* click distance, but a fast worker can
  reach a URL through a longer path before a slow worker reaches it through a shorter one,
  record it too deep, and cut off its links. Doing it correctly means crawling level by level
  (finish every page at depth *d* before starting *d + 1*) whenever a depth limit is set.
- **Rate limiting and adaptive concurrency:** a per-host token bucket plus `Crawl-delay`
  support, and backing off automatically when 429/503 rates or latency go up (AIMD, like TCP
  congestion control).
- **Sitemap seeding:** read `sitemap.xml` (linked from robots.txt) to discover pages that
  nothing links to.
- **Canonical and duplicate-content handling:** honour `<link rel=canonical>` and hash response
  bodies to spot the same content served at different URLs.
- **`rel=nofollow` / `<meta name=robots>` support**, as an option.
- **Charset edge cases:** Lexbor sniffs encoding well, but passing the HTTP `charset` header
  explicitly would be more reliable for legacy encodings.
- **Resumable crawls:** save the queue and seen-set to SQLite so a long crawl survives a
  restart.
- **Observability:** structured logs and metrics (pages/s, latency percentiles, error rates by
  status code).
- **Property-based tests** (Hypothesis) for `normalize_url`, such as checking it is idempotent
  and that relative resolution round-trips.

### Extending to many domains, and why a CLI stops being the right shape

The core pieces already separate cleanly. `Crawler` takes a `Fetcher` and an `on_page`
callback and knows nothing about stdout or argparse, so the engine can be reused. What changes
at scale:

1. **Several domains in one process.** Run one `Crawler` per domain under an
   `asyncio.TaskGroup`, sharing one HTTP client but with **per-host limits**, so a slow site
   can't starve the others and a fast site can't be hammered. The scope becomes a set of allowed
   hosts, or a rule such as "registrable domain via the Public Suffix List" if subdomains should
   count.
2. **Many machines.** One event loop eventually runs out of bandwidth or CPU. The URL queue moves
   into a shared queue (Redis Streams, SQS, Kafka) and the seen-set into a shared store (Redis
   set, or a Bloom filter for memory). URLs are **partitioned by host** (consistent hashing) so
   each host is owned by exactly one worker, which keeps per-host politeness simple and correct.
   Workers are stateless and scale horizontally.
3. **A service instead of a CLI.** A CLI suits one-off, interactive, single-site runs. For many
   domains, the needs are different: long-running jobs that outlive a terminal session,
   scheduling and recurring crawls, progress tracking, cancellation, retries across restarts,
   results stored for querying instead of printed, and multiple users. That points to:
   - an **HTTP API** (e.g. FastAPI): `POST /crawls` returns a job ID, then
     `GET /crawls/{id}` for status and `GET /crawls/{id}/pages` for paginated results;
   - a **job queue / workflow engine** (Celery, Arq, or Temporal for durable multi-step jobs)
     running the workers;
   - a **database** for results (Postgres for the page→link graph, or object storage plus a
     columnar format such as Parquet for large crawls);
   - optionally a small **web dashboard** on top of the API for watching and managing crawls.

   The CLI would still be useful as a thin client of that API, or for local single-site runs,
   with both sharing the same engine package.
