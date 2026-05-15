# ENG-01 operator guide -- selecting and wiring a spot-price feed

**Case id**: `ENG-01__spot_electricity_monitor__home`.
**Release lane**: v3.13.0 ENG-01 first slice.
**Risk class**: informational.
**Status**: CLI supports both local sample input and operator-selected HTTP
JSON feed input.

## Purpose

This guide shows how an operator maps a public hourly spot-price JSON
feed into the ENG-01 CLI so the solver can answer: which are the 3
cheapest hours to use electricity in the next 24 hours?

The guide is provider-neutral. It does not recommend or certify any
real provider URL, tariff service, portal, or vendor API.

## Two ways to run the CLI

Use `--input` for an already-fetched local JSON file:

```powershell
python -m waggledance.adapters.cli.eng01_recommend --input examples/eng01/offline_prices_sample.json --pretty
```

Use `--url` for an operator-selected public HTTP JSON feed:

```powershell
python -m waggledance.adapters.cli.eng01_recommend --url https://prices.example.test/day-ahead.json --pretty
```

The `--input` path is best for repeatable release checks, one-shot
manual experiments, and offline debugging. The `--url` path is best when
the operator has verified a stable public JSON feed and knows how to map
its response fields.

## Provider selection checklist

Before wiring a feed into `--url`, verify by hand that:

1. The feed publishes hour-level prices for the target horizon.
2. The hour values are UTC timestamps or can be interpreted as UTC.
3. The feed exposes a public HTTP or HTTPS endpoint.
4. The endpoint returns JSON with `application/json` or
   `application/ld+json` content type.
5. The response contains a list of rows, either at the root or under a
   stable nested path.
6. Each row contains one hour field and one numeric price field.
7. The price unit is known: `EUR_per_MWh` or `EUR_per_kWh`.
8. The response is fresh enough for the default 12-hour stale threshold,
   or the operator has a reason to override `--stale-threshold-hours`.

Do not add real provider credentials, tokens, cookies, account ids, or
session material to this repository. If a feed needs secrets, treat it
as a separate scoped integration rather than as the public ENG-01 path.

## Walking through a response shape

If the provider returns a flat list with the default keys, no mapping is
needed:

```json
[
  {"hour_utc": "2026-01-16T00:00:00Z", "price": 67.0},
  {"hour_utc": "2026-01-16T01:00:00Z", "price": 38.0}
]
```

If the provider nests rows and uses different key names, inspect one
response and map the fields explicitly:

```json
{
  "data": {
    "prices": [
      {"timestamp_utc": "2026-01-16T00:00:00Z", "eur_per_mwh": 67.0},
      {"timestamp_utc": "2026-01-16T01:00:00Z", "eur_per_mwh": 38.0}
    ]
  }
}
```

That shape maps to:

```powershell
python -m waggledance.adapters.cli.eng01_recommend `
  --url https://prices.example.test/day-ahead.json `
  --rows-path data,prices `
  --hour-key timestamp_utc `
  --price-key eur_per_mwh `
  --price-unit EUR_per_MWh `
  --pretty
```

`--rows-path` accepts comma- or dot-separated path elements. For the
example above, `data,prices` and `data.prices` both point at the rows
array.

## Smart defaults

The URL mode derives several fields automatically:

* `fetched_at_utc` defaults to the current UTC time.
* `horizon_start_utc` defaults to the first parsed row's hour field.
* `feed_source` defaults to a hostname-derived label.
* `horizon_hours` defaults to 24.
* `stale_threshold_hours` defaults to 12.
* `price_unit` defaults to `EUR_per_MWh`.

Override these only when the feed shape or operator requirement differs:

```powershell
python -m waggledance.adapters.cli.eng01_recommend `
  --url https://prices.example.test/day-ahead.json `
  --horizon-start-utc 2026-01-16T00:00:00Z `
  --fetched-at-utc 2026-01-15T20:00:00Z `
  --feed-source operator_selected_public_spot_price_feed `
  --pretty
```

## Optional HTTP headers

If a public feed needs non-secret headers, keep them in a local JSON file
outside committed docs and pass:

```powershell
python -m waggledance.adapters.cli.eng01_recommend `
  --url https://prices.example.test/day-ahead.json `
  --headers-file C:\path\to\local_headers.json
```

The headers file must be a JSON object with string keys and string
values. Credential-like headers are refused by default.
`--allow-credential-headers` is an explicit opt-in and should be treated
as a last resort, not the normal public-feed path.

## Refusal boundaries

The URL path is intentionally fail-closed:

| Refusal surface | Meaning |
|-----------------|---------|
| URL scheme refused | Only HTTP and HTTPS are accepted. |
| URL userinfo refused | Credentials embedded in URLs are refused. |
| localhost / loopback / private host refused | Local and private-network targets are refused. |
| credential marker refused | Secret-like query keys or headers are refused by default. |
| header injection refused | Header values with control characters are refused. |
| timeout or size cap refused | Timeout and response-size settings are bounded. |
| HTTP status refused | Non-2xx HTTP responses are refused. |
| response too large | Response exceeded `--max-response-bytes`. |
| content type refused | Response was not JSON or JSON-LD. |
| empty or malformed response body | Body could not be decoded as JSON. |
| rows path target is not a list | `--rows-path` does not point at an array. |
| row missing key / wrong type | `--hour-key` or `--price-key` does not match the rows. |
| stale data refused | Feed freshness exceeded `--stale-threshold-hours`. |
| missing hour refused | Required hourly data is incomplete. |
| non-monotonic horizon refused | Duplicate or out-of-order hours were detected. |

On refusal the CLI exits with code 2 and prints JSON on stderr with
`result_marker` set to `INVALID_INPUT_REFUSED`.

## Troubleshooting

If `rows path target is not a list` appears, inspect the provider JSON
and update `--rows-path`.

If `row N missing key X` or `row N field X has wrong type` appears,
check `--hour-key`, `--price-key`, and the provider's actual row shape.
Hour values must be strings; price values must be numeric and not null
or boolean.

If `STALE_DATA_REFUSED` appears, the provider data is older than the
configured freshness threshold. Prefer a fresher feed. Raise
`--stale-threshold-hours` only when the operator has explicitly accepted
the age of the data.

If a URL refusal appears, confirm that the target is a public HTTP or
HTTPS URL and does not point at localhost, a private LAN host, or a URL
that embeds credentials.

## What this guide does not cover

This guide does not cover:

* specific provider URLs, schemas, or authentication flows
* private portal or browser-session scraping
* storing credentials between invocations
* scheduled execution or SituationRoom rendering
* controlling chargers, boilers, relays, or any other external effect

ENG-01 still stops at advisory output. Automation belongs behind the
appropriate WriteRCOGate risk class and operator approval policy.

## Source evidence

The guide is based on the v3.13.0 modules that implement the shipped
path:

* CLI flags and smart defaults:
  `waggledance/adapters/cli/eng01_recommend.py`
* HTTP transport refusal boundaries:
  `waggledance/core/v3_13_0/eng01_price_feed_http_transport.py`
* Response parser refusal boundaries:
  `waggledance/core/v3_13_0/eng01_price_feed_response_parser.py`
* Feed adapter and solver freshness / horizon checks:
  `waggledance/core/v3_13_0/eng01_price_feed_adapter.py`
  and `waggledance/core/v3_13_0/eng01_spot_electricity.py`
