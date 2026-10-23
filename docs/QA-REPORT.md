# QA report: 2026-10-23

Scope: the whole product as a tester would use it, against the running stack:
- staff signup → setup → requests → knowledge → settings → team;
- customer sign-in by phone + username, without the bank's link → help chat → report an issue → activity → sign out;
- an API probe of 35 edge cases (validation, authentication, isolation between companies and customers, limits, inbound email).

Tools: an exploratory API script, a Playwright browser walk-through, and the automated suites.

## Bugs found and fixed

| # | Severity | Bug | Root cause | Fix | Pinned by |
|---|---|---|---|---|---|
| 1 | High | A chat message of only spaces gave a 500 (connection dropped) | `min_length=1` checked *before* the text was stripped; the empty text then failed when the workflow was created | `validation.non_blank()`: strip, then validate, on every free-text input (chat, issues, names, company names, questions, emails, customer refs) | `test_qa_regressions.py` |
| 2 | High | Blank names (`"   "`) accepted at signup | Same root cause as #1 | Same fix | `test_blank_names_are_rejected_at_signup` |
| 3 | High | A per-minute model rate limit dead-lettered customer requests in ~20 s (customer told "something went wrong") | Retries backed off 2.5–10 s, so all 3 attempts landed inside the same rate-limit minute | Router raises `ProviderUnavailableError` when every model was rate-limited or unreachable; the worker then backs off ~20/40/80/160 s, and attempts go from 3 to 5 | `test_provider_outage_retries_later_than_other_failures`, `test_all_rate_limited_is_a_provider_outage_not_a_bad_task` |
| 4 | Medium | The test suite failed when run twice within 10 minutes, or after manual QA | Tests shared Redis db 0 with the dev stack; rate-limit keys leaked between runs | Tests use Redis db 15, emptied before every test; the suite passes twice in a row | conftest `settings` fixture |
| 5 | Low | `"abcdefghijk"` accepted as a phone number | Only the length was checked | `Phone` type: 11–13 digits after normalizing | `test_phone_must_be_a_number` |
| 6 | Low | Bank-choice and industry cards centered their content | The base button's `justify-content: center` leaked into the card grid | `.choice { justify-content: stretch }` | visual check |
| 7 | Low | The customer header showed "Your bank" right after sign-in | The bank name arrived only with `/me` | The bank name from the login step is kept in the customer session | `customer-login-page.spec.ts` |
| 8 | Low | Validation errors didn't say which field ("Input should be ≥ 0") | The UI showed pydantic's message alone | `errorMessage` prefixes the field: "Monthly queries: input should be …" | `errors.spec.ts` |

## Verified working (no change needed)

- **Signup wizard:** blocks empty and skipped steps with clear messages.
- **Isolation between companies:**
  - separate ledgers per company;
  - another company's `X-Org-Id` → 403;
  - a code issued for bank A is refused at bank B.
- **Customer sign-in:**
  - phone + username, with any case and spacing;
  - local (`0300…`) and international (`+92…`) phone formats;
  - a bank chooser when the customer has accounts at several banks;
  - unknown details look the same as known ones, and lookups are rate-limited.
- **Customer and staff are separate:**
  - a customer session opening a staff page → redirected to the staff login;
  - a customer token on staff APIs → 401, and a staff token on customer APIs → 401;
  - a customer can't open another customer's chat (404).
- **Input limits:** over-long messages (422); binary and empty uploads (415 / 422); inbound email with a bad sender or empty body (422).
- **Customer UI:** with a blank draft, the Send button is replaced by the mic.
- **Real model replies:** help answer in 27 s; issue reply in 20 s.

## Open (not fixed)

- **Duplicate company names in the bank chooser.** Test companies share names, so the chooser can't tell them apart. Real banks have distinct names; showing a city or branch would disambiguate.
- **Email previews run subject and body together** ("Refund request SYSTEM NOTE: …"). This is cosmetic.
- **AI outcomes for the 8 sample emails couldn't be verified today:** Groq's free quota was exhausted. Rerun `make journeys` and load the sample inbox when the quota resets or the Cerebras key is added.
- **In-flight polls are cancelled at sign-out** (`ERR_ABORTED` in the console). This is harmless.
