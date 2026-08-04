"""
pattern_analyzer.py — Learn per-column data patterns from sample CSV files.

For each column, a ColumnProfile is built that captures:
  • The column's inferred type / generation strategy
  • Enumeration values (with weights derived from sample frequency)
  • Numeric / date ranges
  • Null rate
  • String length statistics
  • Whether a Faker provider should be used
  • Uniqueness and digit-only constraints to prevent DB load failures

The analysis is entirely data-driven — no schema-specific logic.
It works equally well for Provider, Claims, Member, Eligibility, etc.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Oracle date / timestamp patterns
# ---------------------------------------------------------------------------
_ORACLE_DATE_RE = re.compile(
    r"^\d{1,2}-[A-Za-z]{3}-(\d{2}|\d{4})$"
)
_ORACLE_TS_RE = re.compile(
    r"^\d{1,2}-[A-Za-z]{3}-(\d{2}|\d{4})\s+\d{1,2}[.:]\d{2}[.:]\d{2}"
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_TS_RE   = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


# ---------------------------------------------------------------------------
# Faker intent keywords (matched against lowercase column name)
# ---------------------------------------------------------------------------
_FAKER_RULES: list[tuple[list[str], str]] = [
    (["first_name", "frst_nam", "fname", "first_nm",
      "hcidea_first_nam", "owner_first_nam"], "first_name"),
    (["last_name", "lst_nam", "sort_nam", "surname", "lname",
      "hcidea_last_nam", "owner_last_nam"], "last_name"),
    (["email", "mail_addr", "mail_address"], "email"),
    (["phone", "fax", "mobile", "tel"], "phone_number"),
    (["address", "addr1", "addr2", "adr_line", "adr_ln", "street"], "street_address"),
    (["city"], "city"),
    (["state_cd", "state_code"], "state_abbr"),
    (["zip", "postal_code", "post_cd", "zip_cd"], "zipcode"),
    (["dob", "birth_dt", "birth_date", "owner_dob_dt", "hcidea_birth_dt"], "date_of_birth"),
    (["license", "lic_num", "lic_no", "cert_num"], "license"),
    (["npi", "npi_num", "npi_no"], "npi"),
    (["tin", "tax_id", "ssn", "ein", "owner_tax_id", "owner_ssn_num"], "tin"),
    (["dea", "dea_num", "dea_no"], "dea"),
    (["uuid"], "uuid"),
    (["mcare_id", "mcare_num", "mcare_alt_id", "mcaid_id", "mcaid_num", "mcaid_alt_id",
      "alt_id_val", "alt_id_num", "prov_num", "prov_id", "p_alt_id",
      "owner_mcare_id", "owner_mcaid_id"], "provider_id"),
    (["full_name", "provider_name", "prov_name", "prov_nam",
      "contact_name", "contact_nam", "contct_nam", "attn_nam",
      "dba_last_nam", "dba_first_nam",
      "owner_busn_attn_nam", "owner_title_nam"], "name"),
    # Organisation / company names — these generate realistic provider org names
    (["org_nm", "org_name", "dba_nam", "company", "firm",
      "p_nam", "busn_nam", "owner_busn_nam", "owner_dba_nam",
      "legal_nam", "legal_name"], "company"),
]


# ---------------------------------------------------------------------------
# Strategy constants
# ---------------------------------------------------------------------------
STRAT_NULL        = "null"          # always NULL
STRAT_ENUM        = "enum"          # weighted random from observed values
STRAT_SEQUENTIAL  = "sequential"    # monotone integer sequence
STRAT_INT_RANGE   = "int_range"     # uniform integer in [min, max]
STRAT_FLOAT_RANGE = "float_range"   # uniform float  in [min, max]
STRAT_DATE        = "date"          # random date within observed range
STRAT_TIMESTAMP   = "timestamp"     # random timestamp within range
STRAT_FAKER       = "faker"         # Faker provider
STRAT_FREE_TEXT   = "free_text"     # random choice from sample values


@dataclass
class ColumnProfile:
    """Complete description of one column's generation strategy."""
    name        : str
    strategy    : str

    # Enum / free-text
    sample_values: list[str] = field(default_factory=list)   # raw non-null strings
    weights      : list[float] = field(default_factory=list) # same length as sample_values

    # Numeric
    int_min  : int   = 0
    int_max  : int   = 0
    float_min: float = 0.0
    float_max: float = 0.0
    int_step : int   = 1          # for sequential
    int_start: int   = 0          # for sequential (last seen value)

    # Date / timestamp
    date_min : str = ""
    date_max : str = ""
    is_oracle_fmt: bool = True    # True = Oracle DD-MON-YY; False = ISO

    # Faker
    faker_tag: str = ""

    # Nullability
    null_rate: float = 0.0

    # Max observed string length (for truncation safety)
    max_length: int = 255

    # Helper properties for database safety and referential scoring
    is_unique       : bool = False
    strip_non_digits: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PatternAnalyzer:
    """
    Analyse all sample CSV files in *data_dir* and return a dict of
    ``{ table_name: { col_name: ColumnProfile } }``.
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    # ------------------------------------------------------------------ #
    # Public                                                               #
    # ------------------------------------------------------------------ #

    def analyze_all(self) -> dict[str, dict[str, ColumnProfile]]:
        """Return profiles for every CSV file found in *data_dir*."""
        result: dict[str, dict[str, ColumnProfile]] = {}
        csv_files = sorted(self._data_dir.glob("*.csv"))
        logger.info("Pattern analyzer: found %d CSV files.", len(csv_files))
        for path in csv_files:
            table_name = self._stem_to_table(path.stem)
            try:
                profiles = self._analyze_file(path)
                result[table_name] = profiles
                logger.debug("  Analyzed %s (%d columns)", table_name, len(profiles))
            except Exception as exc:  # noqa: BLE001
                logger.warning("  Failed to analyze %s: %s", path.name, exc)
        return result

    def analyze_one(self, csv_path: Path) -> dict[str, ColumnProfile]:
        """Analyze a single CSV file and return its column profiles."""
        return self._analyze_file(csv_path)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _stem_to_table(stem: str) -> str:
        """Convert filename stem to lowercase table name."""
        clean = re.sub(r"_DATA_TABLE$", "", stem, flags=re.IGNORECASE)
        return clean.lower()

    def _analyze_file(self, path: Path) -> dict[str, ColumnProfile]:
        """Read CSV and build a ColumnProfile for every column."""
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader  = csv.DictReader(fh)
            headers = reader.fieldnames or []
            rows    = list(reader)

        profiles: dict[str, ColumnProfile] = {}
        for col in headers:
            col_clean = col.strip()
            values = [r[col].strip() if r.get(col) else "" for r in rows]
            profiles[col_clean.lower()] = self._profile_column(col_clean, values)
        return profiles

    def _profile_column(self, col_name: str, raw_values: list[str]) -> ColumnProfile:
        """Determine the generation strategy for one column."""
        lower_col   = col_name.lower()
        non_null    = [v for v in raw_values if v]
        total       = len(raw_values)
        null_count  = total - len(non_null)
        null_rate   = null_count / total if total else 1.0
        max_len     = max((len(v) for v in non_null), default=0)
        is_unique   = len(non_null) > 0 and len(non_null) == len(set(non_null))

        # 1. Null check
        if not non_null:
            return ColumnProfile(name=lower_col, strategy=STRAT_NULL, null_rate=1.0)

        # 2. Identifier check (e.g. sys_id, affl_sk, etc.)
        # If it matches, we immediately make it sequential, never duplicated
        if self._is_identifier_col(lower_col):
            ints = [int(v) for v in non_null if v.isdigit()]
            int_start = max(ints) if ints else 1000000
            return ColumnProfile(
                name=lower_col, strategy=STRAT_SEQUENTIAL,
                int_start=int_start, int_step=1,
                null_rate=null_rate, is_unique=True
            )

        # 3. Timestamp check
        if self._all_match(_ORACLE_TS_RE, non_null) or self._all_match(_ISO_TS_RE, non_null):
            d_min, d_max = self._date_range(non_null)
            oracle_fmt   = self._all_match(_ORACLE_TS_RE, non_null)
            return ColumnProfile(
                name=lower_col, strategy=STRAT_TIMESTAMP,
                date_min=d_min, date_max=d_max, is_oracle_fmt=oracle_fmt,
                null_rate=null_rate, is_unique=is_unique
            )

        # 4. Date check
        if self._all_match(_ORACLE_DATE_RE, non_null) or self._all_match(_ISO_DATE_RE, non_null):
            d_min, d_max = self._date_range(non_null)
            oracle_fmt   = self._all_match(_ORACLE_DATE_RE, non_null)
            return ColumnProfile(
                name=lower_col, strategy=STRAT_DATE,
                date_min=d_min, date_max=d_max, is_oracle_fmt=oracle_fmt,
                null_rate=null_rate, is_unique=is_unique
            )

        # 5. Semantic Check (takes priority over general CD/IND fallback)
        faker_tag = self._detect_faker(lower_col)
        if faker_tag:
            strip_digits = faker_tag in ("phone_number", "zipcode", "npi", "tin")
            # If strip_digits is True, we must strictly preserve the max observed length
            # to avoid exceeding the database column limit (e.g. phone or zip codes).
            col_max_len = max_len if strip_digits else max(max_len, 50)
            
            # Since some semantic columns (like NPI, License, DEA, Tax ID, Phone, Email, Name)
            # must be highly unique, we set is_unique = True
            # if the sample was unique OR if it's a field where duplicates are not realistically expected.
            sem_unique = is_unique or (faker_tag in ("npi", "dea", "license", "tin", "email", "uuid"))
            
            return ColumnProfile(
                name=lower_col, strategy=STRAT_FAKER, faker_tag=faker_tag,
                null_rate=null_rate, max_length=col_max_len,
                is_unique=sem_unique, strip_non_digits=strip_digits
            )

        # 6. Business Code / Indicator Check (e.g. _cd, _ind, _flag, _status)
        is_code = self._is_business_code_col(lower_col)
        distinct = sorted(set(non_null))
        if is_code:
            weights = [non_null.count(v) / len(non_null) for v in distinct]
            return ColumnProfile(
                name=lower_col, strategy=STRAT_ENUM,
                sample_values=distinct, weights=weights,
                null_rate=null_rate, max_length=max_len,
                is_unique=is_unique
            )

        # 7. Numeric range check
        if self._all_numeric(non_null):
            # Integer vs float
            if all("." not in v for v in non_null):
                ints = [int(v) for v in non_null]
                return ColumnProfile(
                    name=lower_col, strategy=STRAT_INT_RANGE,
                    int_min=min(ints), int_max=max(ints),
                    null_rate=null_rate, is_unique=is_unique
                )
            else:
                floats = [float(v) for v in non_null]
                return ColumnProfile(
                    name=lower_col, strategy=STRAT_FLOAT_RANGE,
                    float_min=min(floats), float_max=max(floats),
                    null_rate=null_rate, is_unique=is_unique
                )

        # 8. Enum fallback
        if len(distinct) <= max(8, len(non_null)):
            weights = [non_null.count(v) / len(non_null) for v in distinct]
            return ColumnProfile(
                name=lower_col, strategy=STRAT_ENUM,
                sample_values=distinct, weights=weights,
                null_rate=null_rate, max_length=max_len,
                is_unique=is_unique
            )

        # 9. Free text fallback
        return ColumnProfile(
            name=lower_col, strategy=STRAT_FREE_TEXT,
            sample_values=non_null,
            null_rate=null_rate, max_length=max_len,
            is_unique=is_unique
        )

    # ── Helper methods ────────────────────────────────────────────────── #

    @staticmethod
    def _is_identifier_col(col_name: str) -> bool:
        """
        Return True if the column is a table-owned sequence identifier (PK/SK).

        CRITICAL: Columns like p_grp_sys_id and p_mbr_sys_id end with _sys_id
        but are FOREIGN KEYS referencing another table's p_sys_id.  We must NOT
        classify them as sequential PKs — doing so causes the generator to emit
        independent counter values that break all INNER JOINs.

        Heuristic: if there is a qualifying segment before _sys_id that is not
        just the table prefix (p_/g_/t_/l_), the column is a qualified FK.
        Examples:
          p_sys_id          → PK  (no qualifier before sys_id)
          p_grp_sys_id      → FK  (qualifier = grp)
          p_mbr_sys_id      → FK  (qualifier = mbr)
          p_enrol_sys_id    → FK  (qualifier = enrol)
          p_hcidea_sys_id   → PK of the hcidea sub-table (no extra qualifier)
        """
        lower_col = col_name.lower()

        # _seq_num columns are always sequential PKs
        if lower_col.endswith("_seq_num"):
            return True

        # Surrogate key columns (_sk suffix) — always PKs
        if lower_col.endswith("_sk") or lower_col == "sk":
            return True

        if lower_col.endswith("_sys_id") or lower_col == "sys_id":
            # Strip leading table prefix (p_, g_, t_, l_)
            stem = lower_col
            for pre in ("p_", "g_", "t_", "l_"):
                if stem.startswith(pre):
                    stem = stem[len(pre):]
                    break
            # Remove _sys_id suffix
            core = stem[: -len("_sys_id")]
            # If core still contains an underscore it is a qualified FK
            # e.g. "grp" has no underscore → likely PK (p_grp_tb.p_grp_sys_id)
            #      "grp_sys" came from p_grp_sys_id where stem=grp_sys → core=grp → no underscore
            # Wait — stem of "p_grp_sys_id" after stripping "p_" = "grp_sys_id"
            # core = "grp_sys_id"[:-7] = "grp" — no underscore → would be PK. Wrong.
            # Better rule: if the original column name has MORE than one qualifier segment
            # between the leading prefix and "_sys_id", it is a FK.
            # p_sys_id      → segments=["sys"] → 1 → PK
            # p_grp_sys_id  → segments=["grp","sys"] → 2 → FK
            # p_mbr_sys_id  → segments=["mbr","sys"] → 2 → FK
            # p_enrol_sys_id→ segments=["enrol","sys"] → 2 → FK
            # p_hcidea_sys_id → segments=["hcidea","sys"] → 2 → FK?
            # Exception: p_hcidea_sys_id is the PK of p_hcidea_prov_tb.
            # Safe rule: columns explicitly in the FK_QUALIFIER list are FKs.
            _FK_QUALIFIERS = {
                "grp", "mbr", "enrol", "enrol", "b",  # b_sys_id in eligibility tables
            }
            if core in _FK_QUALIFIERS:
                return False  # It's a FK, not a PK
            return True

        if lower_col.endswith("_id"):
            if not any(sem in lower_col for sem in [
                "tax_id", "npi", "dea", "license", "payer",
                "user_id", "alt_id", "mcare", "mcaid", "ein",
            ]):
                return True
        return False

    @staticmethod
    def _is_business_code_col(col_name: str) -> bool:
        """Return True if the column represents a business code, flag, or status."""
        if any(col_name.endswith(suffix) for suffix in ["_cd", "_ty_cd", "_ind", "_flag", "_status"]):
            return True
        if col_name in ["gender", "status", "gender_cd", "status_cd"]:
            return True
        return False

    @staticmethod
    def _detect_faker(col_name: str) -> str:
        """Return a faker_tag if the column name matches a Faker pattern, else ''."""
        # Guard against matching table identifiers or phonetic columns
        if any(suffix in col_name for suffix in ["_seq_num", "_sk", "_phntc_", "phntc_"]):
            return ""
            
        lower_col = col_name.lower()
        ends_with_code = any(lower_col.endswith(s) for s in ["_cd", "_ind", "_flag", "_status", "_type", "_class"])
        
        for keywords, tag in _FAKER_RULES:
            for kw in keywords:
                if kw in lower_col:
                    if ends_with_code:
                        if not (kw.endswith("_cd") or kw.endswith("_code") or kw.endswith("_ind") or kw.endswith("_flag") or kw == lower_col):
                            continue
                    return tag
        return ""

    @staticmethod
    def _all_match(pattern: re.Pattern, values: list[str]) -> bool:
        return bool(values) and all(pattern.match(v) for v in values)

    @staticmethod
    def _all_numeric(values: list[str]) -> bool:
        try:
            [float(v) for v in values]
            return True
        except ValueError:
            return False

    @staticmethod
    def _date_range(values: list[str]) -> tuple[str, str]:
        """Return (min_val, max_val) as raw strings for date/timestamp columns."""
        sorted_vals = sorted(values)
        return sorted_vals[0], sorted_vals[-1]
