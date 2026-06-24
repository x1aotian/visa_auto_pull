from __future__ import annotations

import argparse
import hashlib
import json
import os
import smtplib
import ssl
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional


SUPABASE_URL = "https://sdetncywjtheyqwfzshc.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNkZXRuY3l3anRoZXlxd2Z6c2hjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NTQxMTMsImV4cCI6MjA4OTIzMDExM30."
    "QGZoQrxIZ9pKbMSou5QEklxVfmV_p1G9imJW3soQwqM"
)

CITY_ALIASES = {
    "cnBEI": ("beijing", "北京"),
    "cnSHA": ("shanghai", "上海"),
    "cnGUA": ("guangzhou", "广州"),
    "cnSHE": ("shenyang", "沈阳"),
    "cnWUH": ("wuhan", "武汉"),
    "cnCHE": ("chengdu", "成都"),
    "hkHON": ("hong kong", "香港"),
    "krSEO": ("seoul", "首尔"),
    "jpTKY": ("tokyo", "东京"),
    "sgSGP": ("singapore", "新加坡"),
}


@dataclass(frozen=True)
class Match:
    city_key: str
    city_name: str
    visa_class: str
    slot_date: str
    times: tuple[str, ...]
    updated_at: str
    source_timestamp: Optional[float]

    @property
    def fingerprint(self) -> str:
        raw = "|".join(
            [
                self.city_key,
                self.visa_class,
                self.slot_date,
                ",".join(self.times),
                self.updated_at,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def csv_env(name: str) -> list[str]:
    value = env(name)
    return [part.strip() for part in value.split(",") if part.strip()]


def normalize(value: str) -> str:
    return " ".join(value.lower().split())


def parse_yyyy_mm_dd(value: str, name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"{name} must use YYYY-MM-DD, got {value!r}") from exc


def get_city_name(row: dict[str, Any]) -> str:
    data = row.get("data") or {}
    name = str(data.get("visa_type_name") or row.get("city_key") or "")
    if " - " in name:
        return name.rsplit(" - ", 1)[-1].strip()
    return name.strip()


def city_matches(row: dict[str, Any], targets: list[str]) -> bool:
    if not targets:
        return True
    city_key = str(row.get("city_key") or "")
    city_name = get_city_name(row)
    haystack = {normalize(city_key), normalize(city_name)}
    haystack.update(normalize(alias) for alias in CITY_ALIASES.get(city_key, ()))
    return any(normalize(target) in haystack for target in targets)


def visa_matches(visa_class: str, targets: list[str]) -> bool:
    if not targets:
        return True
    normalized = normalize(visa_class)
    return any(normalize(target) in normalized for target in targets)


def fetch_slot_rows() -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"select": "data,updated_at,city_key,visa_class"})
    url = f"{SUPABASE_URL}/rest/v1/slot_data?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
            "Accept": "application/json",
            "Connection": "close",
            "User-Agent": "visa-slot-monitor/1.0",
        },
    )
    last_error: Optional[BaseException] = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase request failed: HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1 + attempt)

    try:
        return fetch_slot_rows_with_curl(url)
    except Exception as curl_exc:
        raise RuntimeError(
            "Could not fetch qmq slot data with Python urllib or curl. "
            "On macOS this is often caused by the Command Line Tools Python TLS stack. "
            "Try Homebrew Python (`brew install python`) or deploy with GitHub Actions/Cloudflare. "
            f"urllib error: {last_error}; curl error: {curl_exc}"
        ) from curl_exc


def fetch_slot_rows_with_curl(url: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            "curl",
            "-fsSL",
            "--max-time",
            "30",
            "-H",
            f"apikey: {SUPABASE_ANON_KEY}",
            "-H",
            f"Authorization: Bearer {SUPABASE_ANON_KEY}",
            "-H",
            "Accept: application/json",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def find_matches(rows: list[dict[str, Any]]) -> list[Match]:
    cities = csv_env("TARGET_CITIES")
    visa_terms = csv_env("TARGET_VISA_TYPES")
    start = parse_yyyy_mm_dd(env("DATE_FROM"), "DATE_FROM")
    end = parse_yyyy_mm_dd(env("DATE_TO"), "DATE_TO")
    if end < start:
        raise SystemExit("DATE_TO must be on or after DATE_FROM")

    matches: list[Match] = []
    for row in rows:
        data = row.get("data") or {}
        attrs = data.get("attrs") or {}
        visa_class = str(row.get("visa_class") or attrs.get("visa_class") or "")
        if not city_matches(row, cities) or not visa_matches(visa_class, visa_terms):
            continue

        slots = data.get("slots") or {}
        if not isinstance(slots, dict):
            continue

        for slot_date, slot_infos in sorted(slots.items()):
            current = parse_yyyy_mm_dd(slot_date, f"slot date {slot_date}")
            if not start <= current <= end:
                continue
            times = []
            if isinstance(slot_infos, list):
                for item in slot_infos:
                    if isinstance(item, dict) and item.get("time"):
                        times.append(str(item["time"]))
            matches.append(
                Match(
                    city_key=str(row.get("city_key") or ""),
                    city_name=get_city_name(row),
                    visa_class=clean_text(visa_class),
                    slot_date=slot_date,
                    times=tuple(times),
                    updated_at=str(row.get("updated_at") or ""),
                    source_timestamp=safe_float(data.get("timestamp")),
                )
            )
    return matches


def clean_text(value: str) -> str:
    return " ".join(value.split())


def safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data.get("seen", []))


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "seen": sorted(seen)[-1000:],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def format_message(matches: list[Match]) -> tuple[str, str]:
    title = f"US visa slot alert: {len(matches)} match(es)"
    lines = [
        title,
        "",
        f"Checked at: {datetime.now(timezone.utc).isoformat()}",
        f"Target cities: {env('TARGET_CITIES') or 'any'}",
        f"Target visa types: {env('TARGET_VISA_TYPES') or 'any'}",
        f"Date range: {env('DATE_FROM')} to {env('DATE_TO')}",
        "",
    ]
    for match in matches[:30]:
        time_text = ", ".join(match.times) if match.times else "time not listed"
        lines.extend(
            [
                f"- {match.slot_date} {time_text}",
                f"  City: {match.city_name} ({match.city_key})",
                f"  Visa: {match.visa_class}",
                f"  Updated: {match.updated_at}",
                "",
            ]
        )
    if len(matches) > 30:
        lines.append(f"...and {len(matches) - 30} more matches.")
    lines.append("Source: https://qmq.app/")
    return title, "\n".join(lines)


def send_email(subject: str, body: str) -> None:
    username = env("GMAIL_USERNAME")
    password = env("GMAIL_APP_PASSWORD")
    recipients = csv_env("EMAIL_TO")
    if not username or not password or not recipients:
        print("Email skipped: set GMAIL_USERNAME, GMAIL_APP_PASSWORD, and EMAIL_TO.")
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)
    print(f"Email sent to {', '.join(recipients)}.")


def send_wechat(subject: str, body: str) -> None:
    serverchan_key = env("SERVERCHAN_SENDKEY")
    if serverchan_key:
        url = f"https://sctapi.ftqq.com/{serverchan_key}.send"
        form = urllib.parse.urlencode({"title": subject, "desp": body}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
        print("ServerChan WeChat notification sent.")
        return
    print("WeChat skipped: set SERVERCHAN_SENDKEY.")


def run(dry_run: bool = False) -> int:
    required = ["DATE_FROM", "DATE_TO"]
    missing = [name for name in required if not env(name)]
    if missing:
        raise SystemExit(f"Missing required env var(s): {', '.join(missing)}")

    rows = fetch_slot_rows()
    matches = find_matches(rows)
    print(f"Fetched {len(rows)} rows; found {len(matches)} matching slots.")
    if not matches:
        return 0

    state_path = Path(env("STATE_FILE", ".cache/seen.json"))
    seen = load_seen(state_path)
    new_matches = [match for match in matches if match.fingerprint not in seen]
    print(f"{len(new_matches)} new matching slots after dedupe.")
    if not new_matches:
        return 0

    subject, body = format_message(new_matches)
    print(body)
    if not dry_run:
        send_email(subject, body)
        send_wechat(subject, body)
        seen.update(match.fingerprint for match in new_matches)
        save_seen(state_path, seen)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Monitor qmq.app US visa appointment slots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Example:
              TARGET_CITIES=cnGUA TARGET_VISA_TYPES=F-1 DATE_FROM=2026-07-01 DATE_TO=2026-08-31 \\
                python -m visa_monitor.monitor --dry-run
            """
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print matches without sending notifications.")
    args = parser.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
