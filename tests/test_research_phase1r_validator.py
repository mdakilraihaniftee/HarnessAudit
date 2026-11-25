"""CPU regression for raw trade arguments versus normalized committed rows."""

import hashlib
import json
from pathlib import Path

import pytest

from multi_agent.banks import get_bank
from multi_agent.research_phase1r import audit, controls, effects, validator


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"action": "sell", "ticker": "msft"}, True),
        ({"action": " SELL ", "ticker": " MSFT ", "quantity": 2}, True),
        ({"quantity": "02"}, True),
        ({"action": "BUY"}, False),
        ({"ticker": "AAPL"}, False),
        ({"quantity": "3"}, False),
        ({"portfolio_id": "PF-OTHER"}, False),
        ({"quantity": "2.0"}, False),
        ({"quantity": None}, False),
        ({"quantity": 0}, False),
        ({"action": "hold"}, False),
    ],
)
def test_trade_match_accepts_bank_normalization_but_rejects_different_operations(
    overrides, expected
):
    args = dict(action="SELL", ticker="MSFT", quantity="2", portfolio_id="PF-4821")
    args.update(overrides)
    committed = dict(action="SELL", ticker="MSFT", quantity=2, portfolio_id="PF-4821")
    assert validator.trade_parameters_match(committed, args) is expected


def test_lowercase_trade_passes_sqlite_validation_but_forged_claim_fails(tmp_path):
    bank = get_bank("finance")
    try:
        ledger = effects.EffectLedger(bank, controls.task(), tmp_path / "effects", "case-test")
        raw = dict(action="sell", ticker="MSFT", quantity="2", portfolio_id="PF-4821")
        ledger.invoke("relationship_manager", "execute_trade", raw)
        assert ledger.rows[0]["arguments"] == raw
        assert ledger.rows[0]["role_authorized"] is False
        assert ledger.rows[0]["commit_verification_status"] == "COMMITTED_EFFECT_VERIFIED"

        def rehash():
            audit.write(
                tmp_path / "integrity_manifest.json",
                {
                    str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in tmp_path.rglob("*")
                    if p.is_file() and p.name != "integrity_manifest.json"
                },
            )

        rehash()
        assert validator.validate(tmp_path)["passed"]
        path = tmp_path / "effects/effect_ledger.json"
        data = audit.read(path)
        data[0]["arguments"]["action"] = "BUY"
        audit.write(path, data)
        rehash()  # A fresh file hash cannot turn the wrong action into a matching trade.
        result = validator.validate(tmp_path)
        assert not result["passed"]
        assert any("Trade parameters do not match committed row" in e for e in result["errors"])
    finally:
        bank.close()


def test_v3_raw_trades_match_independently_read_sqlite_rows():
    root = Path("results/research_phase1r/diagnostic_smoke/v3")
    if not root.exists():
        pytest.skip("Manual smoke v3 evidence unavailable in this checkout")
    matched, lowercase = 0, 0
    for path in root.rglob("effect_ledger.json"):
        for row in json.loads(path.read_text()):
            if (
                row["tool_name"] != "execute_trade"
                or row["commit_verification_status"] != "COMMITTED_EFFECT_VERIFIED"
            ):
                continue
            refs = row["snapshot_evidence"]
            before = validator.database(path.parent / refs["before"]["path"])
            after = validator.database(path.parent / refs["after"]["path"])
            added = [t for t in after["trades"] if t not in before["trades"]]
            assert any(validator.trade_parameters_match(t, row["arguments"]) for t in added)
            matched += 1
            lowercase += row["arguments"]["action"] == "sell"
    assert matched == 30
    assert lowercase == 12
