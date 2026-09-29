from eval.form_equivalence import equal, rescore


def test_same_day_written_differently():
    assert equal("July 15", "2026-07-15")
    assert equal("October 7th", "2026-10-07")
    assert not equal("October 5", "2026-10-07")


def test_numbers_booleans_and_words():
    assert equal(1800, "1800")
    assert equal(200, 200.0)
    assert not equal(1800, "800.0")
    assert equal(True, "true")
    assert equal("PO999", "PO-999")
    assert equal("winter jackets", "winter jacket")
    assert not equal("Las Vegas", "Vegas")
    assert not equal("gift", "electronics")


def test_rescore_never_forgives_tools_or_missing_arguments():
    def scenario(passed, tools_ok, details):
        return {"scenario_id": "x", "passed": passed,
                "checks": {"tool_selection": {"passed": tools_ok},
                           "argument_accuracy": {"details": details}}}
    form_only = [{"passed": False, "expected_args": {"date": "July 15"}, "actual_args": {"date": "2026-07-15"}}]
    missing = [{"passed": False, "expected_args": {"pets_allowed": True}, "actual_args": {}}]
    report = {"scenario_results": [scenario(True, True, []), scenario(False, True, form_only),
                                   scenario(False, False, form_only), scenario(False, True, missing)]}
    strict, flipped, _ = rescore(report)
    assert (strict, flipped) == (1, 1)
