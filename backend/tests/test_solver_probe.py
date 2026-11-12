from dealcockpit.adapters.solver_probe import PRESETS, build_and_solve, load_inputs


def test_probe_inputs_are_self_contained_and_solvable():
    inputs = load_inputs("H1_nameplate_per_month")
    assert len(inputs["groups"]) > 50
    r = build_and_solve(inputs, "SCIP", PRESETS["B_max_profit"], False, 30.0)
    assert r["status"] == "OPTIMAL"
    assert 0.0 <= r["accept"]["PL-02"] <= 1.0


def test_same_input_same_result_deterministic():
    inputs = load_inputs("H1_nameplate_per_month")
    a = build_and_solve(inputs, "SCIP", PRESETS["A_protect_customers"], False, 30.0)
    b = build_and_solve(inputs, "SCIP", PRESETS["A_protect_customers"], False, 30.0)
    assert a["objective_krw"] == b["objective_krw"] and a["accept"] == b["accept"]


def test_capacity_interpretation_changes_the_recommendation():
    # 결정 불변성 게이트가 필요한 이유: 해석이 다르면 추천(증설 선택·수락률)이 달라진다 (스파이크 한정 수치)
    w = PRESETS["B_max_profit"]
    h1 = build_and_solve(load_inputs("H1_nameplate_per_month"), "SCIP", w, False, 30.0)
    h3 = build_and_solve(load_inputs("H3_calibrated_from_batchlog"), "SCIP", w, False, 30.0)
    assert (h1["opt02"], h1["accept"]) != (h3["opt02"], h3["accept"])
