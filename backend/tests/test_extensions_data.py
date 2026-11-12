"""S1 데이터 무결성: 합성/모의 자료가 도메인 스키마를 만족하고, 원본은 그대로이며, 모든 추가 자료가 출처 표시됨."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dealcockpit.domain.clause import Clause
from dealcockpit.domain.common import Provenance
from dealcockpit.domain.plant import OutsourcingPartner, PremiumCosts
from dealcockpit.domain.workflow import Reply, ReplyOutcome

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "extensions"
RAW = ROOT.parent / "01_참가자_배포용" / "04_원본데이터"
PAGE_MARK = "--- [p."


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def label_files():
    return sorted((EXT / "contracts" / "labels").glob("*.labels.json"))


def doc_text(doc_id: str) -> str:
    return next((EXT / "contracts").rglob(f"{doc_id}.md")).read_text(encoding="utf-8")


# ── 계약서 정답 라벨 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", label_files(), ids=lambda p: p.stem)
def test_gold_labels_satisfy_domain_clause_schema_and_point_at_exact_text(path):
    lab = load(path)
    text = doc_text(lab["doc_id"])
    for c in lab["clauses"]:
        rec = {k: v for k, v in c.items() if k != "extra_evidence"}
        clause = Clause.model_validate(rec)  # 근거 인용 필수·kind별 파라미터 검증 포함
        s, e = c["evidence"]["char_span"]
        assert text[s:e] == c["evidence"]["quote"], c["clause_id"]
        # 페이지: 해당 위치 직전의 페이지 표식과 일치
        before = text[:s]
        page = int(before.rsplit(PAGE_MARK, 1)[1].split("]", 1)[0])
        assert page == c["evidence"]["page"], c["clause_id"]
        assert clause.usable_by_solver  # 정답 라벨은 인간 확정본
    for item in lab["unmapped"] + lab["flags"]:
        if "quote" in item:
            s, e = item["char_span"]
            assert text[s:e] == item["quote"]


def test_demo_contracts_cover_the_killer_clauses():
    kinds = {}
    for p in label_files():
        lab = load(p)
        for c in lab["clauses"]:
            kinds.setdefault(c["customer_id"], set()).add(c["kind"])
    assert {"termination_trigger", "price_discount_waiver", "late_penalty", "partial_shipment"} <= kinds["C-101"]
    assert {"fixed_delivery_slot", "late_penalty"} <= kinds["C-102"]


def test_c102_conflict_between_body_and_special_terms_is_labeled_not_silently_resolved():
    lab = load(EXT / "contracts" / "labels" / "CT-C102.labels.json")
    assert lab["conflicts"] and "특약" in " ".join(lab["conflicts"][0]["between"])
    assert "확인 카드" in lab["conflicts"][0]["expected_behavior"]


def test_adversarial_label_expects_refusal_to_invent_or_obey():
    lab = load(EXT / "contracts" / "labels" / "CT-ADV-01.labels.json")
    assert lab["clauses"] == []  # 누락 파라미터를 지어내면 안 되므로 유효 조항 0건
    codes = {f["code"] for f in lab["flags"]}
    assert {"missing_param", "prompt_injection"} <= codes
    assert lab["conflicts"]


def test_contract_documents_are_marked_synthetic():
    for p in (EXT / "contracts").rglob("*.md"):
        head = p.read_text(encoding="utf-8")[:400]
        assert "합성 문서(가상)" in head, p.name


def test_c102_slot_supports_the_dermal_collision_story():
    lab = load(EXT / "contracts" / "labels" / "CT-C102.labels.json")
    slot = next(c for c in lab["clauses"] if c["kind"] == "fixed_delivery_slot")
    assert slot["params"]["product_id"] == "PL-02" and slot["hard"] is True
    assert slot["params"]["qty"] == 120_000 and slot["params"]["slot_date"] == "2026-09-12"


# ── 외주·특근·전환 가정 ──────────────────────────────────────────────────────
def test_outsourcing_profile_parses_into_domain_models_and_is_not_source():
    prof = load(EXT / "synthetic" / "outsourcing_profile.json")
    premium = PremiumCosts.model_validate(prof["premium"])
    assert {p.partner_id for p in premium.partners} == {"V-OUT-1", "V-OUT-2"}
    assert premium.provenance is Provenance.ASSUMPTION
    assert all(p.provenance is Provenance.SYNTHETIC for p in premium.partners)
    assert isinstance(premium.partners[0], OutsourcingPartner)


# ── 모의 회신: 유리한 회신만 있으면 안 된다 ───────────────────────────────────────
def mock_variants():
    pol = load(EXT / "synthetic" / "mock_reply_policy.json")
    for topic, t in pol["topics"].items():
        for v in t["variants"]:
            yield topic, t, v


def test_every_mock_reply_topic_includes_unfavorable_outcomes():
    pol = load(EXT / "synthetic" / "mock_reply_policy.json")
    for topic, t in pol["topics"].items():
        outcomes = {v["outcome"] for v in t["variants"]}
        assert outcomes & {"REJECT", "COUNTER", "CONDITIONAL", "NO_REPLY"}, f"{topic}: 유리한 회신만 있음"
        assert any(not v["favorable"] for v in t["variants"]), topic


@pytest.mark.parametrize("topic,t,v", list(mock_variants()), ids=lambda x: x if isinstance(x, str) else None)
def test_mock_replies_are_valid_simulated_replies(topic, t, v):
    r = Reply(reply_id="RP-001", request_id="IR-001", raw_text=v["raw_text"], outcome=ReplyOutcome(v["outcome"]),
              terms=v["terms"], provenance=Provenance.SIMULATED)
    assert r.usable_for_replan
    if v["outcome"] == "NO_REPLY":
        assert v["delay_days"] is None and v["raw_text"] == ""
    else:
        assert v["raw_text"].strip() and v["delay_days"] >= 0


def test_unfavorable_variants_do_not_claim_to_be_favorable():
    for topic, t, v in mock_variants():
        if v["outcome"] in ("REJECT", "NO_REPLY"):
            assert v["favorable"] is False, (topic, v["variant"])


# ── MANIFEST / 원본 보존 ─────────────────────────────────────────────────────
def test_manifest_covers_every_extension_file_with_provenance():
    manifest = load(EXT / "MANIFEST.json")
    listed = {i["path"] for i in manifest["items"]}
    actual = {p.relative_to(EXT).as_posix() for p in EXT.rglob("*") if p.is_file() and p.name != "MANIFEST.json"}
    assert actual == listed, f"누락 {actual - listed}, 유령 {listed - actual}"
    for i in manifest["items"]:
        assert i["provenance"] in {"SYNTHETIC", "SIMULATED", "DERIVED"}
        assert i["reason"] and i["applies_at"] and i["used_in"] is not None
        assert "anchors" in i


def test_raw_data_room_is_untouched():
    from hashlib import sha256

    rec = load(EXT / "raw_checksums.json")["files"]
    current = {p.relative_to(RAW).as_posix(): sha256(p.read_bytes()).hexdigest() for p in RAW.rglob("*") if p.is_file()}
    assert current == rec


def test_extensions_do_not_reference_edits_inside_raw_folder():
    # 합성 자료는 원본 폴더 밖(extensions/)에만 존재한다
    assert not any(p.is_file() for p in RAW.rglob("*synthetic*"))
