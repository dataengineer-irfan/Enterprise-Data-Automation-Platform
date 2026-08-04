"""
smoke_test.py — Quick syntax and import validation for the converter package.

Forces UTF-8 stdout so it works on Windows terminals with cp1252 default.

Run this before using the converter to confirm all modules are importable
and the core classes can be instantiated.

Usage:
    python smoke_test.py
"""
import sys
import traceback

import io
import os
# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "PASS"
FAIL = "FAIL"

results: list[tuple[str, bool, str]] = []


def check(label: str, fn) -> None:
    try:
        fn()
        results.append((label, True, ""))
    except Exception:
        results.append((label, False, traceback.format_exc()))


# ── Module imports ──────────────────────────────────────────────────────────
check("import config",          lambda: __import__("config"))
check("import db",              lambda: __import__("db"))
check("import datatype_mapper", lambda: __import__("datatype_mapper"))
check("import ddl_converter",   lambda: __import__("ddl_converter"))
check("import sql_executor",    lambda: __import__("sql_executor"))
check("import data_loader",     lambda: __import__("data_loader"))
check("import converter",       lambda: __import__("converter"))

# ── Class instantiation ─────────────────────────────────────────────────────
def _mapper_instantiate():
    from datatype_mapper import DataTypeMapper
    m = DataTypeMapper()
    assert m.map_type("NUMBER(10,0)") == "BIGINT"
    assert m.map_type("VARCHAR2(200)") == "VARCHAR(200)"
    assert m.map_type("DATE") == "DATE"
    assert m.map_type("CLOB") == "TEXT"
    assert m.map_type("TIMESTAMP(6)") == "TIMESTAMP"

check("DataTypeMapper instantiate + spot-checks", _mapper_instantiate)


def _converter_instantiate():
    from ddl_converter import DDLConverter
    c = DDLConverter(schema="provider")
    assert c.schema == "provider"

check("DDLConverter instantiate", _converter_instantiate)


def _convert_minimal():
    from ddl_converter import DDLConverter
    c = DDLConverter(schema="provider")

    sample = """
    CREATE TABLE "MYSCHEMA"."PATIENT_TB"
    (
        "PATIENT_ID"   NUMBER(10,0)       NOT NULL ENABLE,
        "PATIENT_NM"   VARCHAR2(200)      NOT NULL ENABLE,
        "BIRTH_DT"     DATE               DEFAULT SYSDATE NOT NULL ENABLE,
        "STATUS_CD"    NUMBER(1,0)        DEFAULT 0 NOT NULL ENABLE,
        "NOTES"        CLOB,
        CONSTRAINT "PK_PATIENT_TB" PRIMARY KEY ("PATIENT_ID")
        USING INDEX PCTFREE 10 INITRANS 2 MAXTRANS 255
        TABLESPACE "USERS" ENABLE,
        CONSTRAINT "FK_PATIENT_DEPT" FOREIGN KEY ("STATUS_CD")
        REFERENCES "MYSCHEMA"."DEPT_TB" ("DEPT_ID") ENABLE
    ) SEGMENT CREATION IMMEDIATE
      PCTFREE 10 PCTUSED 40 INITRANS 1 MAXTRANS 255
      NOCOMPRESS LOGGING
      STORAGE(INITIAL 65536 NEXT 1048576 MINEXTENTS 1 MAXEXTENTS 2147483645)
      TABLESPACE "USERS";

    CREATE UNIQUE INDEX "MYSCHEMA"."IDX_PATIENT_NM"
        ON "MYSCHEMA"."PATIENT_TB" ("PATIENT_NM")
        PCTFREE 10 INITRANS 2 MAXTRANS 255 TABLESPACE "USERS";

    COMMENT ON TABLE "MYSCHEMA"."PATIENT_TB" IS 'Patient master table';
    COMMENT ON COLUMN "MYSCHEMA"."PATIENT_TB"."PATIENT_ID" IS 'Primary key';
    """

    pg_stmts, fk_stmts = c._convert_content(sample)

    assert any("CREATE TABLE provider.patient_tb" in s for s in pg_stmts), \
        f"CREATE TABLE not found. Got: {pg_stmts}"
    assert any("DROP TABLE IF EXISTS provider.patient_tb" in s for s in pg_stmts), \
        "DROP TABLE not found"
    assert any("CREATE UNIQUE INDEX" in s for s in pg_stmts), \
        "Index not found"
    assert any("COMMENT ON TABLE" in s for s in pg_stmts), \
        "Table comment not found"
    assert len(fk_stmts) == 1, f"Expected 1 FK, got {len(fk_stmts)}"
    assert "ADD CONSTRAINT" in fk_stmts[0], "FK ALTER TABLE not generated"

    # Verify type conversions
    combined = "\n".join(pg_stmts)
    assert "bigint" in combined.lower(),  "NUMBER(10,0) not mapped to BIGINT"
    assert "varchar(200)" in combined.lower(), "VARCHAR2 not converted"
    assert "CURRENT_TIMESTAMP" in combined, "SYSDATE not converted"
    assert "text" in combined.lower(), "CLOB not mapped to TEXT"
    assert "TABLESPACE" not in combined, "TABLESPACE not stripped"
    assert "SEGMENT CREATION" not in combined, "SEGMENT CREATION not stripped"
    assert "STORAGE" not in combined, "STORAGE not stripped"

check("DDLConverter end-to-end conversion", _convert_minimal)


# ── Report ──────────────────────────────────────────────────────────────────
print()
print("=" * 55)
print("  Smoke Test Results")
print("=" * 55)
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)

for label, ok, err in results:
    icon = PASS if ok else FAIL
    print(f"  {icon}  {label}")
    if err:
        for line in err.strip().splitlines():
            print(f"       {line}")

print("-" * 55)
print(f"  {passed}/{total} checks passed")
print("=" * 55)
print()

if passed < total:
    sys.exit(1)
