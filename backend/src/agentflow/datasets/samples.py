"""Realistic sample files for demos, generated fresh (trips must be upcoming). Each one has a
deliberately bad row, so the review step has something to catch."""

import csv
import io
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode()


def customers() -> bytes:
    return _csv(
        ["Cust ID", "Account Title", "Mobile No", "Login", "E-mail"],
        [
            ["CUST-2001", "Ali Haider", "0301 5550101", "ali.haider", "ali.haider@example.com"],
            ["CUST-2002", "Mariam Siddiqui", "+92 302 5550102", "mariam.s", "mariam@example.com"],
            ["CUST-2003", "Kashif Mehmood", "03035550103", "kashif.m", ""],
            ["CUST-2004", "Nida Farooq", "0300-12", "nida.f", "nida@example"],  # bad phone, email
        ],
    )


def transactions() -> bytes:
    day = datetime.now(UTC) - timedelta(days=1)
    when = day.strftime("%d/%m/%Y")
    return _csv(
        [
            "Txn Ref",
            "Customer ID",
            "Value Date",
            "Narration",
            "Amount (PKR)",
            "Dr/Cr",
            "Channel",
            "OTP",
        ],
        [
            [
                "TX-77001",
                "CUST-1001",
                f"{when} 09:15",
                "Salary credit",
                "PKR 95,000",
                "Cr",
                "ibft",
                "yes",
            ],
            [
                "TX-77002",
                "CUST-1001",
                f"{when} 12:40",
                "K-Electric bill",
                "6,250",
                "Dr",
                "app",
                "yes",
            ],
            [
                "TX-77003",
                "CUST-1002",
                f"{when} 18:05",
                "Daraz",
                "(3,499.00)",
                "",
                "card_online",
                "yes",
            ],
            [
                "TX-77004",
                "CUST-1002",
                f"{when} 21:30",
                "IBFT to Hamza (MCB)",
                "10,000",
                "Dr",
                "ibft",
                "yes",
            ],
            [
                "TX-77005",
                "CUST-1003",
                f"{when} 23:55",
                "Unknown merchant",
                "abc",
                "Dr",
                "card_online",
                "no",
            ],
        ],
    )


def trips() -> bytes:
    # Local times, as a Pakistani operator's schedule would list them (no zone in the file).
    local = datetime.now(UTC).astimezone(ZoneInfo("Asia/Karachi")).replace(tzinfo=None)
    base = (local + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)
    rows = []
    services = [
        ("KHI-LHE", "Karachi", "Lahore", 7, 18, "6,500", 40, "Executive AC"),
        ("KHI-LHE", "Karachi", "Lahore", 17, 3, "6,500", 40, "Executive AC"),
        ("KHI-ISB", "Karachi", "Islamabad", 9, 22, "7,200", 40, "Business Class"),
        ("LHE-ISB", "Lahore", "Islamabad", 8, 12, "2,800", 45, "Standard AC"),
        ("LHE-ISB", "Lahore", "Islamabad", 14, 0, "2,800", 45, "Standard AC"),
        ("LHE-MUX", "Lahore", "Multan", 10, 25, "2,100", 45, "Standard AC"),
    ]
    for day in (0, 1):
        for code, frm, to, hour, seats, fare, total, bus in services:
            dep = base.replace(hour=hour) + timedelta(days=day)
            hours = {"KHI-LHE": 18, "KHI-ISB": 20, "LHE-ISB": 5, "LHE-MUX": 5}[code]
            rows.append(
                [
                    f"{code}-{dep:%m%d}-{hour:02d}",
                    frm,
                    to,
                    f"{dep:%Y-%m-%d %H:%M}",
                    f"{dep + timedelta(hours=hours):%Y-%m-%d %H:%M}",
                    fare,
                    str(seats),
                    str(total),
                    "scheduled",
                    bus,
                    "",
                ]
            )
    rows[3][8] = "delayed"
    rows[3][10] = "Departure moved 45 minutes later: motorway closure"
    rows.append(
        [
            "LHE-MUX-BAD",
            "Lahore",
            "Multan",
            "tomorrow evening",
            "",
            "2,100",
            "twelve",
            "45",
            "scheduled",
            "Standard AC",
            "",
        ]
    )  # bad time and seat count
    return _csv(
        [
            "Trip ID",
            "From",
            "To",
            "Departure",
            "Arrival",
            "Fare (PKR)",
            "Seats Left",
            "Capacity",
            "Status",
            "Bus Type",
            "Remarks",
        ],
        rows,
    )


SAMPLES = {"customers": customers, "transactions": transactions, "trips": trips}
