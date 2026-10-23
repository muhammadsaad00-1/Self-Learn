"""Record types a company can import, per industry, and the code that turns raw cells into
clean values. Pure functions: no database, no model.

A record type is a list of fields with a kind, whether it is required, and the header
synonyms a real export might use ("Cust ID", "Mobile No"). The same rules check a CSV row, a
row a person typed, and a row the extraction agent read from a PDF: whatever the source, only
values that pass them reach the database.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel

Kind = Literal[
    "text", "money", "integer", "datetime", "boolean", "phone", "email", "choice", "cnic"
]


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: Kind = "text"
    required: bool = False
    synonyms: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    hint: str = ""
    max_length: int = 200


@dataclass(frozen=True)
class RecordType:
    name: str
    label: str
    description: str
    fields: tuple[Field, ...]

    @property
    def key(self) -> str:
        return self.fields[0].name  # the natural key: re-importing updates, never duplicates


CUSTOMERS = RecordType(
    "customers",
    "Customers",
    "Who your customers are: the agents match messages, logins and emails to them.",
    (
        Field(
            "customer_ref",
            "Customer ID",
            required=True,
            max_length=100,
            synonyms=(
                "customer id",
                "cust id",
                "customer no",
                "customer number",
                "cif",
                "account id",
                "passenger id",  # transport companies: their customers are passengers
                "passenger no",
                "member id",
                "id",
            ),
        ),
        Field(
            "customer_name",
            "Name",
            required=True,
            synonyms=("name", "full name", "customer name", "account title", "title"),
        ),
        Field(
            "phone",
            "Mobile",
            "phone",
            synonyms=("mobile", "mobile no", "phone number", "cell", "contact"),
        ),
        Field(
            "cnic",
            "CNIC",
            "cnic",
            max_length=32,
            hint="Optional 13-digit ID used to verify seat bookings; stored as a keyed digest.",
            synonyms=("national id", "national identity number", "identity card", "id card"),
        ),
        Field(
            "username",
            "Username",
            max_length=64,
            hint="They sign in to the customer app with it.",
            synonyms=("user name", "login", "user id"),
        ),
        Field(
            "email", "Email", "email", max_length=320, synonyms=("email address", "e-mail", "mail")
        ),
        Field(
            "currency",
            "Currency",
            synonyms=("ccy", "account currency"),
            max_length=3,
            hint="Default PKR",
        ),
    ),
)

TRANSACTIONS = RecordType(
    "transactions",
    "Transactions",
    "Customers' account activity: the evidence the agents check before refunding, reversing "
    "or answering a dispute.",
    (
        Field(
            "reference",
            "Reference",
            required=True,
            max_length=50,
            synonyms=(
                "ref",
                "transaction id",
                "txn id",
                "txn ref",
                "transaction reference",
                "stan",
                "rrn",
            ),
        ),
        Field(
            "customer_ref",
            "Customer ID",
            required=True,
            max_length=100,
            synonyms=("customer id", "cust id", "account id", "customer no", "cif"),
        ),
        Field(
            "occurred_at",
            "Date and time",
            "datetime",
            required=True,
            synonyms=(
                "date",
                "transaction date",
                "value date",
                "posting date",
                "time",
                "timestamp",
            ),
        ),
        Field(
            "description",
            "Description",
            required=True,
            synonyms=("merchant", "narration", "details", "particulars", "beneficiary", "payee"),
        ),
        Field(
            "amount", "Amount", "money", required=True, synonyms=("amount (pkr)", "value", "amt")
        ),
        Field(
            "direction",
            "Debit or credit",
            "choice",
            required=True,
            choices=("debit", "credit"),
            synonyms=("type", "dr/cr", "debit/credit", "transaction type", "direction"),
        ),
        Field(
            "channel",
            "Channel",
            "choice",
            choices=("card_online", "pos", "atm", "app", "ibft", "raast", "branch"),
            synonyms=("source", "medium"),
        ),
        Field(
            "transfer_status",
            "Transfer status",
            "choice",
            choices=("credited", "pending", "failed"),
            synonyms=("network status", "beneficiary status", "status"),
        ),
        Field("device", "Device", synonyms=("device name", "terminal")),
        Field(
            "device_known", "Registered device", "boolean", synonyms=("known device", "registered")
        ),
        Field(
            "verified",
            "OTP / 3-D Secure verified",
            "boolean",
            synonyms=("otp", "3ds", "otp verified", "authenticated"),
        ),
    ),
)

TRIPS = RecordType(
    "trips",
    "Trips",
    "Your schedule: the agents answer seat, time, fare and delay questions from it.",
    (
        Field(
            "trip_ref",
            "Trip ID",
            required=True,
            max_length=50,
            synonyms=("trip id", "trip no", "service no", "schedule id", "id"),
        ),
        Field(
            "origin",
            "From",
            required=True,
            max_length=100,
            synonyms=("from", "origin city", "departure city", "source"),
        ),
        Field(
            "destination",
            "To",
            required=True,
            max_length=100,
            synonyms=("to", "destination city", "arrival city"),
        ),
        Field(
            "departs_at",
            "Departure",
            "datetime",
            required=True,
            synonyms=("departure", "departure time", "date", "time", "departs"),
        ),
        Field("arrives_at", "Arrival", "datetime", synonyms=("arrival", "arrival time", "arrives")),
        Field(
            "fare", "Fare", "money", required=True, synonyms=("price", "ticket price", "fare (pkr)")
        ),
        Field(
            "seats_available",
            "Seats available",
            "integer",
            required=True,
            synonyms=("available seats", "seats left", "available", "vacant seats"),
        ),
        Field("seats_total", "Total seats", "integer", synonyms=("capacity", "seats")),
        Field(
            "status", "Status", "choice", choices=("scheduled", "delayed", "cancelled", "departed")
        ),
        Field("vehicle", "Vehicle", synonyms=("bus", "bus type", "coach", "class", "service")),
        Field("notes", "Notes", max_length=500, synonyms=("remarks", "comment")),
    ),
)

RECORD_TYPES = {r.name: r for r in (CUSTOMERS, TRANSACTIONS, TRIPS)}


# --- Header mapping ------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9/()]+", " ", text.lower()).strip()


def map_headers(record_type: RecordType, headers: list[str]) -> dict[int, str]:
    """Column index -> field name, by name, label or synonym. Unknown columns are ignored
    (and reported); each field is taken once, from the first matching column."""
    mapping: dict[int, str] = {}
    taken: set[str] = set()
    for exact in (True, False):
        for i, header in enumerate(headers):
            if i in mapping:
                continue
            h = _norm(header)
            for f in record_type.fields:
                if f.name in taken:
                    continue
                names = {_norm(f.name), _norm(f.label), *(_norm(s) for s in f.synonyms)}
                if (h in names) if exact else any(n and n in h for n in names if len(n) > 3):
                    mapping[i] = f.name
                    taken.add(f.name)
                    break
    return mapping


# --- Cell normalization -------------------------------------------------------------------

_TRUE = {"yes", "y", "true", "1", "verified", "registered", "known", "haan", "ha"}
_FALSE = {"no", "n", "false", "0", "unverified", "unknown", "new", "nahi"}
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %I:%M %p",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M",
    "%d-%m-%Y",
    "%d %b %Y %H:%M",
    "%d %b %Y %I:%M %p",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y %I:%M %p",
    "%b %d, %Y",
)


def _money(raw: str) -> Decimal:
    cleaned = re.sub(r"(?i)(pkr|rs\.?|rupees|usd|\$|,|\s)", "", raw)
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    value = Decimal(cleaned.strip("()"))
    return -value if negative else value


def _datetime(raw: str, zone: tzinfo) -> datetime:
    text = re.sub(r"\s+", " ", raw.strip())
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError("not a date") from None
    # A time without a zone is the company's local time (its business profile).
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


def _phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    digits = "92" + digits[1:] if digits.startswith("0") else digits
    if not 11 <= len(digits) <= 13:
        raise ValueError("not a mobile number")
    return digits


def clean_value(f: Field, raw: Any, zone: tzinfo = UTC) -> Any:
    """One cell: raw text -> typed value (or None if empty). Raises ValueError with a
    message a person can act on."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    text = str(raw).strip()
    try:
        match f.kind:
            case "money":
                return str(_money(text).quantize(Decimal("0.01")))
            case "integer":
                number = int(Decimal(text.replace(",", "")))
                if f.name in ("seats_available", "seats_total") and number < 0:
                    raise ValueError("must be zero or more")
                return number
            case "datetime":
                return _datetime(text, zone).isoformat()
            case "boolean":
                low = text.lower()
                if low in _TRUE:
                    return True
                if low in _FALSE:
                    return False
                raise ValueError("expected yes or no")
            case "phone":
                return _phone(text)
            case "cnic":
                digits = re.sub(r"\D", "", text)
                if len(digits) != 13:
                    raise ValueError("enter the 13 digits from the identity card")
                return digits
            case "email":
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
                    raise ValueError("not an email address")
                return text.lower()
            case "choice":
                low = _norm(text).replace(" ", "_")
                aliases = {
                    "dr": "debit",
                    "cr": "credit",
                    "withdrawal": "debit",
                    "deposit": "credit",
                    "success": "credited",
                    "successful": "credited",
                    "rejected": "failed",
                    "in_transit": "pending",
                    "on_time": "scheduled",
                    "canceled": "cancelled",
                }
                low = aliases.get(low, low)
                if low not in f.choices:
                    raise ValueError(f"expected one of {', '.join(f.choices)}")
                return low
            case _:
                value = text[: f.max_length]
                return value.lower() if f.name == "username" else value
    except (InvalidOperation, ArithmeticError):
        raise ValueError("not a number") from None


class Checked(BaseModel):
    data: dict[str, Any]  # clean values, by field name
    errors: dict[str, str]  # field name -> what is wrong


def check_row(record_type: RecordType, raw: dict[str, Any], zone: tzinfo = UTC) -> Checked:
    data: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for f in record_type.fields:
        try:
            value = clean_value(f, raw.get(f.name), zone)
        except ValueError as e:
            errors[f.name] = str(e)
            data[f.name] = raw.get(f.name)  # keep what was there, so a person can fix it
            continue
        if value is None and f.required:
            errors[f.name] = "required"
        data[f.name] = value
    if record_type.name == "transactions" and not errors.get("amount") and data.get("amount"):
        if Decimal(data["amount"]) < 0:  # (5,000.00) in a statement: a debit
            data["amount"] = str(-Decimal(data["amount"]))
            data["direction"] = data.get("direction") or "debit"
            errors.pop("direction", None)
        elif Decimal(data["amount"]) == 0:
            errors["amount"] = "must be more than 0"
    if (
        record_type.name == "trips"
        and not errors
        and data.get("seats_total") is not None
        and data["seats_available"] > data["seats_total"]
    ):
        errors["seats_available"] = "more than the total seats"
    return Checked(data=data, errors=errors)
