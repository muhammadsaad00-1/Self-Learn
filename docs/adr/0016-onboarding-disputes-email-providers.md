# 0016. Business onboarding, dispute intelligence, email channel, second LLM provider

- Status: accepted
- Date: 2026-10-23

## Context
For the hackathon we need:
- a credible signup and business case;
- a strict wall between customers and the team's dashboard;
- an answer to "can it handle a customer who lies about a transaction?";
- the email channel businesses actually drown in;
- relief from Groq free-tier limits (200k tokens/day on gpt-oss-120b, plus per-minute caps, both hit during testing).

## Decision
- **Positioning:** only companies that move money (commercial bank, microfinance bank, wallet/payments, digital lender), enforced by the `Industry` enum. The core is domain-agnostic, but the tools and policies are built for money movement, and a sharp audience pitches better.
- **Signup wizard:** you → business → support today. `organizations.profile` (`BusinessProfile`) sizes the ROI on the Overview: hours and money saved, and a monthly projection with its assumptions shown. Admins edit it in Settings.
- **Customer/team separation:** customer tokens have their own audience. A test walks the OpenAPI schema and asserts every staff operation (34 today) refuses a customer token. The customer app is branded with the bank's name (`/v1/customer/bank/{id}`) and links nowhere into the dashboard.
- **Dispute intelligence (`policy/dispute.py`):** transactions record channel, device (and whether it is registered) and OTP / 3-D Secure.
  - For fraud claims, code scores the disputed payments: unrecognised device, no OTP, new merchant, unusual amount and bursts count toward *unauthorized*; registered device, OTP and a known merchant count toward *customer-made*.
  - A verdict needs a 30-point margin.
  - `likely_customer_made` makes the policy **block** the automatic freeze (`dispute_review`). The ticket gets the summary, and the reply states the facts without accusing.
  - The card tool re-assesses at execution time.
- **Reply language:** triage outputs `language` (en / ur / roman_ur), and the reply agent is told explicitly. A real run had answered an English question in Roman Urdu.
- **Email channel:** `POST /v1/inbound/email/{org}/{token}`, where token = HMAC(secret, org): nothing stored, unguessable, stable.
  - Senders are matched to customers by email; provider retries are deduplicated by `message_id`.
  - A sample inbox of 8 emails (including an injection attempt) is available for demos.
  - Replies are drafted, not sent.
- **Providers:** model names are `provider:model`. With `CEREBRAS_API_KEY`, `cerebras:gpt-oss-120b` (the same open model; free tier 1M tokens/day, 5 requests/min) joins every route after Groq's first choice. Rate-limited models cool down for 60 s, so calls during an outage don't wait on a provider that will refuse.

## Consequences
- Verified with real models:
  - False fraud claim (CUST-1006): no freeze; reply cites the registered device and OTP; ticket opened.
  - Genuine fraud (CUST-1003): card frozen.
  - Records answer: correct.
  - Documents question: stays in the help chat.
- Dispute signals are only as good as the bank's data. A real integration needs the card processor's device and 3-D Secure fields.
- Cerebras' 5 requests/min free tier suits a fallback, not primary traffic.
