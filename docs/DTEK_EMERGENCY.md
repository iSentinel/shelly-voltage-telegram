# DTEK address outage advisory normalizer (draft, offline only)

Source: https://github.com/mr-devboy/dtek-monitor

The upstream browser implementation opens a regional DTEK shutdown
page, obtains its CSRF token, posts a same-origin request to /ua/ajax
(method=getHomeNum), and examines data[house] fields sub_type,
start_date, end_date and type.

This draft introduces **only a pure parser**, with no browser, network
calls, address collection, Telegram send or database writes.
It classifies explicit Ukrainian emergency labels (авар/екстр),
planned labels (план/граф/стабіліз), explicit empty fields (none),
and all missing or ambiguous responses (unknown).

The event_key function excludes provider polling timestamps so
repeated checks do not create duplicate notifications. Different
reported end times create different keys for a future edit/reply flow.

Safety boundaries for future work:

- Never infer electric power state from DTEK text; Shelly is the source
  of real measurements, and TCP connectivity alone is not power proof.
- Missing house, provider timeout, CSRF failure, unsupported labels,
  or stale provider data must yield unknown, not "power available".
- A collector must be isolated from the main voltage poller and
  public channel, and must not upload an owner's address to GitHub.
- Map the correct city/street/house via the site's autocomplete.
- Verify the current DTEK response schema and dates before enabling.
- Only after private acceptance introduce opt-in Telegram rendering;
  this PR does not change any production runtime behavior.

Unit fixtures in tests/test_dtek_events.py use synthetic data only.
