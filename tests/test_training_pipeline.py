"""The router training-data pipeline: label serialisation, the serving-identical render, the validator, and the build."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training import build as build_mod  # noqa: E402
from training import sample as sample_io  # noqa: E402
from training.render import canonical_label, manifests, render  # noqa: E402
from training.sample import Sample  # noqa: E402
from training.validate import validate  # noqa: E402


def call(tool, **args):
    return {"tool": tool, "args": args}


def sample(user="weather in nagpur", calls=None, live=None, clar=None, **kw):
    calls = [call("get_weather", place="Nagpur")] if calls is None else calls
    label = {"needs_live_data": bool(calls) or bool(live), "calls": calls}
    if clar:
        label["clarification"], label["needs_live_data"] = clar, False
    return Sample(id=kw.pop("id", "t1"), category=kw.pop("category", "explicit"), user=user, label=label, **kw)


def errors(s):
    return {i.code for i in validate(s) if i.level == "error"}


# ---- the label as the grammar writes it ---------------------------------------------------------------------------------------

def test_label_is_compact_json_in_the_grammars_key_order():
    label = {"calls": [call("get_weather", place="Nagpur")], "needs_live_data": True}                 # keys given in the wrong order
    assert canonical_label(label) == '{"needs_live_data":true,"calls":[{"tool":"get_weather","args":{"place":"Nagpur"}}]}'


def test_arguments_follow_the_manifest_order_and_none_is_dropped():
    out = canonical_label({"needs_live_data": True, "calls": [call("calendar_create", time="10:00", title="Kickoff", date_offset=1, duration_minutes=None)]})
    assert out == '{"needs_live_data":true,"calls":[{"tool":"calendar_create","args":{"title":"Kickoff","date_offset":1,"time":"10:00"}}]}'


def test_a_clarification_is_written_last_and_only_when_present():
    assert canonical_label({"needs_live_data": False, "calls": [], "clarification": "Which one?"}) == '{"needs_live_data":false,"calls":[],"clarification":"Which one?"}'
    assert canonical_label({"needs_live_data": False, "calls": []}) == '{"needs_live_data":false,"calls":[]}'


def test_non_ascii_is_kept_as_text_not_escaped():
    out = canonical_label({"needs_live_data": True, "calls": [call("tasks", action="add", title="दूध खरीदना")]})
    assert "दूध खरीदना" in out and "\\u" not in out


# ---- the render: the bytes the server sends ---------------------------------------------------------------------------------------

def test_the_render_is_the_serving_prompt_followed_by_the_decision():
    r = render(sample(active="ACTIVE: get_weather | place=Oslo | 2 min ago", history=[{"user": "weather in oslo", "veda": "It is 4 degrees."}]))
    assert r.prompt.startswith("<|im_start|>system\n") and r.prompt.endswith("<|im_end|>\n<|im_start|>assistant\n")
    assert "\n/no_think<|im_end|>\n<|im_start|>assistant\n" in r.prompt
    assert "ACTIVE: get_weather | place=Oslo | 2 min ago" in r.volatile and "User: weather in oslo" in r.volatile and "Veda: It is 4 degrees." in r.volatile
    assert r.volatile.rstrip().endswith("USER: weather in nagpur") and "TODAY: Friday 09 Oct 2026, 18:30." in r.volatile
    assert r.completion == r.label_json + "<|im_end|>" and r.label_json.startswith('{"needs_live_data":true')


def test_with_nothing_active_the_prompt_says_so_exactly_as_serving_does():
    v = render(sample()).volatile
    assert v.splitlines()[0] == "ACTIVE: none" and "RECENT: none" in v


def test_a_tool_subset_changes_the_tool_list_in_the_prompt():
    full, small = render(sample()), render(sample(tools=["get_weather", "tasks"]))
    assert "calendar_create(" in full.system and "calendar_create(" not in small.system and "get_weather(" in small.system


def test_the_render_matches_the_models_own_chat_template_when_the_model_file_is_present():
    gguf = ROOT / "data" / "Qwen3-4B-Q4_K_M.gguf"
    if not gguf.exists():
        pytest.skip("model file not present")
    from llama_cpp import Llama
    from llama_cpp.llama_chat_format import Jinja2ChatFormatter

    llm = Llama(model_path=str(gguf), vocab_only=True, verbose=False)
    fmt = Jinja2ChatFormatter(template=llm.metadata["tokenizer.chat_template"], eos_token="<|im_end|>", bos_token="", stop_token_ids=[llm.token_eos()])
    r = render(sample())
    served = fmt(messages=[{"role": "system", "content": r.system}, {"role": "user", "content": r.volatile + "\n/no_think"}]).prompt
    assert served == r.prompt


# ---- the validator ------------------------------------------------------------------------------------------------------------------

def test_a_good_label_passes():
    assert validate(sample()) == []


@pytest.mark.parametrize("label,code", [
    ({"needs_live_data": True, "calls": [call("get_weather_forecast", place="Pune")]}, "args"),                       # required date_offset missing
    ({"needs_live_data": True, "calls": [call("get_weather", city="Pune")]}, "args"),                               # unknown argument
    ({"needs_live_data": True, "calls": [call("volume_control", action="louder")]}, "args"),                        # not in the enum
    ({"needs_live_data": True, "calls": [call("calendar_agenda", date_offset=9)]}, "args"),                         # beyond 6 days
    ({"needs_live_data": True, "calls": [call("calendar_create", title="Review", time="5pm")]}, "args"),            # not HH:MM
    ({"needs_live_data": True, "calls": [call("calendar_create", title="Review", time="10:00", duration_minutes=2)]}, "args"),
    ({"needs_live_data": True, "calls": [call("book_flight", to="Paris")]}, "tool"),
    ({"needs_live_data": False, "calls": [call("get_weather", place="Pune")]}, "flag"),                             # a call needs the flag
    ({"needs_live_data": False, "calls": [call("get_weather", place="Pune")], "clarification": "Where?"}, "clarification"),
    ({"needs_live_data": True, "calls": [], "clarification": "Which one?"}, "clarification"),                        # asking is flag false
    ({"needs_live_data": False, "calls": [], "clarification": ""}, "clarification"),                                 # key present but empty
    ({"needs_live_data": True, "calls": [call("get_weather", place="A")] * 4}, "calls"),
])
def test_bad_labels_are_rejected(label, code):
    s = Sample(id="x", category="explicit", user="weather in pune", label=label)
    assert code in errors(s)


def test_the_serving_policy_has_the_last_word():
    # "delete it" with a guessed target: serving would ask, so labelling it as a call is wrong
    s = sample(user="delete it", calls=[call("tasks", action="delete", title="milk")])
    assert "policy" in errors(s)
    # a pronoun as the target
    assert "policy" in errors(sample(user="remove that one", calls=[call("tasks", action="delete", title="that one")]))
    # a named target is fine
    assert errors(sample(user="remove the milk task", calls=[call("tasks", action="delete", title="milk")])) == set()


def test_gmail_send_needs_a_draft_that_was_just_read_back():
    assert "policy" in errors(sample(user="send it", calls=[call("gmail_send")]))
    assert errors(sample(user="send it", calls=[call("gmail_send")], active="ACTIVE: gmail_draft | to=a@example.com | just now")) == set()


def test_text_copied_from_a_prompt_example_is_rejected_unless_the_user_said_it():
    assert "leak" in errors(sample(user="put it on my calendar tomorrow at 5 pm", calls=[call("calendar_create", title="Software review", date_offset=1, time="17:00")]))
    assert errors(sample(user="put the software review on my calendar tomorrow at 5 pm", calls=[call("calendar_create", title="Software review", date_offset=1, time="17:00")])) == set()


def test_currency_codes_and_number_words_are_not_flagged_as_ungrounded():
    s = sample(user="convert two hundred dollars to rupees", calls=[call("convert_currency", amount=200, **{"from": "USD", "to": "INR"})])
    assert validate(s) == []
    assert validate(sample(user="remember my locker number is forty two", calls=[call("remember", action="save", topic="locker number", value="42")])) == []


def test_an_invented_free_text_value_is_a_warning_not_an_error():
    s = sample(user="add something to my tasks", calls=[call("tasks", action="add", title="renew car insurance")])
    assert errors(s) == set() and {i.code for i in validate(s)} == {"grounding"}


def test_chat_refuse_and_clarify_labels_are_checked_against_the_policy_too():
    assert errors(sample(user="explain recursion", calls=[], live=False)) == set()
    assert errors(sample(user="how bad is the traffic", calls=[], live=True)) == set()
    assert errors(sample(user="and how about sunday", calls=[], clar="What would you like to know about Sunday?")) == set()


# ---- the build ------------------------------------------------------------------------------------------------------------------------------

def write(tmp_path, samples):
    path = tmp_path / "in.jsonl"
    sample_io.dump(samples, path)
    return path


def run_build(tmp_path, samples, **kw):
    params = dict(exclude_provisional=False, val_percent=20, leak=0.0, evals=[])
    params.update(kw)
    return build_mod.build([write(tmp_path, samples)], tmp_path / "out", **params)


def test_build_drops_invalid_duplicate_provisional_and_eval_copies_and_reports_why(tmp_path):
    good = sample(id="a", user="weather in nagpur")
    dup = sample(id="b", user="Weather in Nagpur!")                       # same words, same context
    bad = sample(id="c", user="weather in pune", calls=[call("get_weather", city="Pune")])
    maybe = sample(id="d", user="weather in kochi", calls=[call("get_weather", place="Kochi")], status="provisional")
    copy = sample(id="e", user="weather in goa", calls=[call("get_weather", place="Goa")])
    report = run_build(tmp_path, [good, dup, bad, maybe, copy], exclude_provisional=True, evals=["Weather in Goa"])
    assert report["loaded"] == 5 and report["provisional_excluded"] == 1 and report["invalid"] == {"args": 1}
    assert report["duplicates_dropped"] == 1 and report["leaked_into_eval_dropped"] == 1 and report["kept"] == 1
    assert report["by_kind"] == {"call": 1} and report["tools_used"] == {"get_weather": 1}


def test_build_writes_prompt_and_completion_pairs_and_splits_by_group(tmp_path):
    samples = [sample(id=f"s{i}", user=f"weather in town {i}", calls=[call("get_weather", place=f"Town {i}")], group=f"g{i // 3}") for i in range(30)]
    run_build(tmp_path, samples)
    out = tmp_path / "out"
    train = [json.loads(line) for line in (out / "train.jsonl").read_text(encoding="utf-8").splitlines()]
    val = [json.loads(line) for line in (out / "val.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(train) + len(val) == 30 and val and train
    assert not ({r["group"] for r in train} & {r["group"] for r in val})              # paraphrases never straddle the split
    assert all(r["prompt"].endswith("<|im_start|>assistant\n") and r["completion"].endswith("<|im_end|>") for r in train + val)
    again = run_build(tmp_path / "again", samples) if (tmp_path / "again").mkdir() is None else None
    assert again["train"] == len(train)                                               # the split is deterministic


def test_build_flags_a_repeated_id(tmp_path):
    report = run_build(tmp_path, [sample(id="same", user="weather in nagpur"), sample(id="same", user="weather in pune", calls=[call("get_weather", place="Pune")])])
    assert report["duplicate_ids"] == ["same"] and report["kept"] == 0


def test_the_pilot_batch_is_valid_end_to_end():
    pilot = ROOT / "training" / "datasets" / "pilot.jsonl"
    if not pilot.exists():
        pytest.skip("pilot not generated")
    samples = sample_io.load(pilot)
    assert len(samples) >= 70
    bad = {s.id: sorted(errors(s)) for s in samples if errors(s)}
    assert bad == {}
    assert {c["tool"] for s in samples for c in s.label["calls"]} >= set(manifests())      # every tool has at least one example


# ---- tool-subset variants -------------------------------------------------------------------------------------------------------------

def test_a_subset_variant_keeps_every_tool_it_calls_and_is_still_a_valid_label():
    import random

    from training.augment import subset_variant

    original = sample(user="weather in goa and convert fifty euros to rupees", calls=[call("get_weather", place="Goa"), call("convert_currency", amount=50, **{"from": "EUR", "to": "INR"})])
    v = subset_variant(original, random.Random(1))
    assert {"get_weather", "convert_currency"} <= set(v.tools) and len(v.tools) < len(manifests()) and v.id == "t1-s" and v.label == original.label
    assert errors(v) == set() and "calendar_create(" not in render(v).system or "calendar_create" in v.tools


def test_clarify_refuse_and_already_restricted_samples_are_never_subset():
    import random

    from training.augment import subset_variant

    rng = random.Random(1)
    assert subset_variant(sample(user="email priya", calls=[], clar="What should the email say?"), rng) is None
    assert subset_variant(sample(user="how bad is the traffic", calls=[], live=True), rng) is None
    assert subset_variant(sample(tools=["get_weather", "tasks"]), rng) is None
    assert subset_variant(sample(user="explain recursion", calls=[], live=False), rng) is not None          # chat may lose any tool


def test_augment_is_deterministic_and_build_keeps_the_variants_next_to_their_originals(tmp_path):
    from training.augment import augment

    base = [sample(id=f"s{i}", user=f"weather in town {i}", calls=[call("get_weather", place=f"Town {i}")]) for i in range(40)]
    assert [v.id for v in augment(base, 0.5)] == [v.id for v in augment(base, 0.5)] and 5 < len(augment(base, 0.5)) < 35
    report = run_build(tmp_path, base, subset_probability=0.5)
    assert report["tool_subset_variants_added"] > 5 and report["kept"] == 40 + report["tool_subset_variants_added"]


def test_a_tool_can_be_capped_by_dropping_whole_paraphrase_groups_deterministically(tmp_path):
    base = [sample(id=f"s{i}", user=f"weather in town {i}", calls=[call("get_weather", place=f"Town {i}")], group=f"g{i // 4}") for i in range(40)]
    other = [sample(id="t1", user="what are my tasks", calls=[call("tasks", action="list")])]
    kept, dropped = build_mod.cap_tools(base + other, {"get_weather": 20})
    weather = [s for s in kept if s.label["calls"][0]["tool"] == "get_weather"]
    assert len(weather) == 20 and dropped == {"get_weather": 20} and any(s.id == "t1" for s in kept)
    assert all(sum(1 for s in weather if s.group == g) in (0, 4) for g in {s.group for s in weather})      # whole groups only
    assert [s.id for s in build_mod.cap_tools(base, {"get_weather": 20})[0]] == [s.id for s in kept if s.id != "t1"]
    assert build_mod.cap_tools(base, {"get_weather": 99})[1] == {}                                        # under the cap: untouched
