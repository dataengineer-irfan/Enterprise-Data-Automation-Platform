"""
backend/sample_rows.py — generates a few plausible rows for a table on the
fly, so the Masking Designer / Schema Explorer screens can show a live
preview for ANY table, not just the ones with a hand-written CSV under
samples/. Column-type/name-aware but intentionally simple (regex-driven
Faker provider selection) — this is preview data for the UI, not the
FK-aware synthetic-data generator (`data_generator.py`, carried over from
the original converter project) which is the real tool for populating an
actual target database and already handles FK integrity properly.
"""
from __future__ import annotations

import random
import re

from faker import Faker

from core.database_port import TableMetadata

_fake = Faker("en_US")
Faker.seed(42)  # deterministic across requests, so a preview doesn't jitter on refresh


def _value_for_column(col) -> str:
    name = col.name.lower()
    dtype = col.data_type.upper()

    if col.is_primary_key and "sys_id" in name:
        return str(random.randint(900001, 900050))
    if re.search(r"ssn|tin", name):
        return str(_fake.random_number(digits=9, fix_len=True))
    if re.search(r"tax_id", name):
        return str(_fake.random_number(digits=9, fix_len=True))
    if re.search(r"npi", name):
        return str(_fake.random_number(digits=10, fix_len=True))
    if re.search(r"lic_cert_num", name):
        return f"{_fake.state_abbr()}-{random.choice(['MD','DO','NP','PA'])}-{_fake.random_number(digits=5, fix_len=True)}"
    if re.search(r"state_cd", name):
        return _fake.state_abbr()
    # audit/system columns (g_aud_*, *_user_id, *_ts, *_ind, *_sk, *_seq_num) show up on
    # nearly every table in this schema — worth real patterns rather than falling through
    # to a random English word, which is harmless but looks like a bug at a glance.
    if re.search(r"user_id$", name):
        return _fake.user_name()
    if re.search(r"_ts$", name) or dtype.startswith("TIMESTAMP"):
        return _fake.date_time_between(start_date="-1y", end_date="now").isoformat(sep=" ")
    if "_dt" in name or name.endswith("dt") or dtype == "DATE":
        return _fake.date_between(start_date="-3y", end_date="today").isoformat()
    if re.search(r"_ind$", name):
        return random.choice(["Y", "N"])
    if re.search(r"_sk$|_seq_num$", name):
        return str(random.randint(1, 999999))
    if re.search(r"ver_num$", name):
        return str(random.randint(0, 12))
    if re.search(r"_cd$", name) and dtype.startswith("VARCHAR"):
        return random.choice(["A1", "B2", "C3", "XX"])
    if dtype.startswith(("BIGINT", "NUMBER", "SMALLINT")):
        return str(random.randint(1, 9999))
    # genuinely free-text columns are rare in this schema (mostly codes/IDs/dates/audit
    # fields, all handled above) — a real word is at least honestly labeled sample data
    # rather than a nonsensical digit string for something that isn't numeric.
    return _fake.word()


def generate_sample_rows(table: TableMetadata, count: int = 3) -> list[dict]:
    rows = []
    for _ in range(count):
        rows.append({col.name: _value_for_column(col) for col in table.columns})
    return rows
