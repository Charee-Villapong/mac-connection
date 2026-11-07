"""Recipient header spec tests (independent, spec-only).

Background: a LINE mis-send happened because recipient verification
searched the whole screen for the recipient name.  The name appearing in
the talk list (left pane) was enough to pass, even though the open talk's
header belonged to a different person.

Spec under test (acceptance criteria written before the fix):

1. ``run_verify_checks``'s "recipient" check judges ONLY the header
   region of the open talk:
   - header region: an OCR item whose centre-x (x + w/2) exceeds
     frame_width * talk-list width ratio, and whose y is below the
     upper bound.  The ratio is ``acfg["talk_list_width_ratio"]`` when
     present, otherwise 0.32.  The y upper bound defaults to 260.
   - items in the region are merged into per-row runs; each run is
     compared against each alias in ``acfg["recipient_aliases"]``.
     An exact match passes; otherwise a match where the run starts
     with the alias and the remainder is at most 2 characters
     ("prefix") also passes.  Anything else fails.
   - a name appearing only in the talk list or elsewhere on screen
     must NOT pass.  No text in the header region means failure.
   - the recipient check dict carries "expected" (the alias list) and
     "actual_header" (the strings read in the header region), and its
     "evidence" states the judgement method (exact / prefix).
   - this applies both in mode="field" (pre-send) and mode="bubble"
     (post-send).
2. ``Runner`` gains a public ``check_recipient_header()`` method that,
   right after navigation opens a talk, checks the recipient against
   the header region only and returns a bool.
3. ``run_post(r, text, ...)`` calls ``r.check_recipient_header()``
   exactly once before the pre-send step.  On False it returns
   ``(False, "wrong_recipient", {"count": 0, "attempts": []})``
   without calling pre_send_once / send / cleanup_wrong_recipient.
   On True it proceeds as before.

All person names in this file are fictional.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# (window_id?, x, y, width, height) -- frame width is 1440, so the
# default 0.32 talk-list boundary is centre-x = 460.8.
WIN = (767, 0, 30, 1440, 870)

ALIAS = "テスト花子"
OTHER = "テスト太郎"

LINE_TEXT = (
    "【AIエージェントからのお知らせ】\n"
    "これはテストです。\n"
    "送信はしません。\n"
    "確認が終わったら消します。"
)

DISCORD_TEXT = (
    "```\n"
    "[SYSTEM] AIエージェントからお知らせ\n"
    "これはテストです。\n"
    "送信はしません。\n"
    "```"
)


def line_acfg(aliases=None, **overrides):
    acfg = {
        "app_name": "LINE",
        "window_app": "LINE",
        "process": "LINE",
        "input_click": [800, 780],
        "nav_click": [210, 160],
        "recipient_aliases": list(aliases) if aliases else [ALIAS],
    }
    acfg.update(overrides)
    return acfg


def discord_acfg(aliases=None, **overrides):
    acfg = {
        "app_name": "Discord",
        "window_app": "Discord",
        "process": "Discord",
        "input_click": [800, 780],
        "nav_click": [210, 160],
        "recipient_aliases": list(aliases) if aliases else [ALIAS],
    }
    acfg.update(overrides)
    return acfg


def ocr_line(x, y, w, h, text):
    return f"TEXT [x={x} y={y} w={w} h={h} conf=1.00] {text}"


# Talk-list item: centre-x = 100 + 200/2 = 200 (left of 460.8 boundary).
def list_item(text, y=60):
    return ocr_line(100, y, 200, 30, text)


# Header item: centre-x = 500 + 200/2 = 600 (right of the boundary),
# y = 60 (below the 260 upper bound).
def header_item(text, y=60, x=500):
    return ocr_line(x, y, 200, 30, text)


def body_lines(expected_text):
    """Expected message placed low on screen (y >= 700), i.e. well
    outside the header region, so it cannot influence the header check.
    """
    return [
        ocr_line(500, 700 + 30 * i, 300, 30, line)
        for i, line in enumerate(expected_text.split("\n"))
    ]


def make_ocr(*items, expected_text=LINE_TEXT):
    return "\n".join([*items, *body_lines(expected_text)])


def recipient_check(result):
    """Extract the 'recipient' check dict from run_verify_checks()."""
    if isinstance(result, dict):
        if result.get("name") == "recipient":
            return result
        candidate = result.get("recipient")
        if isinstance(candidate, dict):
            return candidate
        for value in result.values():
            if isinstance(value, dict) and value.get("name") == "recipient":
                return value
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, dict) and item.get("name") == "recipient":
                return item
            if isinstance(item, dict) and "recipient" in item:
                nested = item["recipient"]
                if isinstance(nested, dict):
                    return nested
    raise AssertionError(f"recipient check not found in result: {result!r}")


def check_ok(check):
    for key in ("ok", "passed", "pass", "success"):
        if key in check:
            return bool(check[key])
    raise AssertionError(f"recipient check has no boolean result: {check!r}")


def verify_recipient(app, acfg, ocr_text, expected_text, mode):
    result = gui_flow.run_verify_checks(
        app, acfg, ocr_text, WIN, expected_text, mode
    )
    return recipient_check(result)


# ---------------------------------------------------------------------------
# a. The original accident: name in talk list, different person in header
# ---------------------------------------------------------------------------


def test_a_line_list_name_does_not_pass_when_header_is_other_person():
    ocr = make_ocr(list_item(ALIAS), header_item(OTHER))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "field")

    assert check_ok(check) is False
    assert check["expected"] == [ALIAS]
    # actual_header reflects what was read in the header region.
    assert OTHER in check["actual_header"]
    assert ALIAS not in check["actual_header"]


# ---------------------------------------------------------------------------
# b. Exact header match passes
# ---------------------------------------------------------------------------


def test_b_line_header_exact_match_passes():
    ocr = make_ocr(header_item(ALIAS))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "field")

    assert check_ok(check) is True
    assert check["expected"] == [ALIAS]
    assert ALIAS in check["actual_header"]
    assert "exact" in check["evidence"]


# ---------------------------------------------------------------------------
# c. Prefix match with at most 2 extra characters passes
# ---------------------------------------------------------------------------


def test_c_line_header_prefix_with_two_extra_chars_passes():
    # ALIAS + "4呼" (2 extra characters)
    ocr = make_ocr(header_item(ALIAS + "4呼"))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "field")

    assert check_ok(check) is True
    assert "prefix" in check["evidence"]


# ---------------------------------------------------------------------------
# d. Prefix match with 3 extra characters fails
# ---------------------------------------------------------------------------


def test_d_line_header_prefix_with_three_extra_chars_fails():
    # ALIAS + "さんへ" (3 extra characters)
    ocr = make_ocr(header_item(ALIAS + "さんへ"))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "field")

    assert check_ok(check) is False


# ---------------------------------------------------------------------------
# e. Name only in the talk list, nothing in the header region -> fails
# ---------------------------------------------------------------------------


def test_e_line_name_only_in_list_and_header_empty_fails():
    ocr = make_ocr(list_item(ALIAS))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "field")

    assert check_ok(check) is False
    assert check["actual_header"] == []


# ---------------------------------------------------------------------------
# f. Discord shape: header decides, talk list does not
# ---------------------------------------------------------------------------


def test_f_discord_header_match_passes():
    ocr = make_ocr(header_item(ALIAS), expected_text=DISCORD_TEXT)
    check = verify_recipient(
        "discord", discord_acfg(), ocr, DISCORD_TEXT, "field"
    )

    assert check_ok(check) is True
    assert ALIAS in check["actual_header"]


def test_f_discord_name_only_in_list_fails():
    ocr = make_ocr(list_item(ALIAS), expected_text=DISCORD_TEXT)
    check = verify_recipient(
        "discord", discord_acfg(), ocr, DISCORD_TEXT, "field"
    )

    assert check_ok(check) is False


# ---------------------------------------------------------------------------
# g. Post-send (bubble) mode also judges by header only
# ---------------------------------------------------------------------------


def test_g_bubble_mode_header_other_person_fails():
    ocr = make_ocr(list_item(ALIAS), header_item(OTHER))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "bubble")

    assert check_ok(check) is False
    assert OTHER in check["actual_header"]


# ---------------------------------------------------------------------------
# Spec 2: Runner.check_recipient_header() exists as a public method
# ---------------------------------------------------------------------------


def test_runner_has_public_check_recipient_header_method():
    method = getattr(gui_flow.Runner, "check_recipient_header", None)
    assert callable(method)


# ---------------------------------------------------------------------------
# Extra spec-derived guards: ratio override and header y upper bound
# ---------------------------------------------------------------------------


def test_talk_list_width_ratio_in_acfg_overrides_default_boundary():
    # ratio 0.5 -> boundary centre-x = 720.  The item at centre-x 600
    # is then on the talk-list side and must not count as header.
    acfg = line_acfg(talk_list_width_ratio=0.5)
    ocr = make_ocr(header_item(ALIAS))
    check = verify_recipient("line", acfg, ocr, LINE_TEXT, "field")

    assert check_ok(check) is False


def test_header_item_below_y_upper_bound_does_not_pass():
    # y = 300 is at/past the default header upper bound (260).
    ocr = make_ocr(header_item(ALIAS, y=300))
    check = verify_recipient("line", line_acfg(), ocr, LINE_TEXT, "field")

    assert check_ok(check) is False


# ---------------------------------------------------------------------------
# h / i. run_post gates on check_recipient_header()
# ---------------------------------------------------------------------------


class FakeRunner:
    """Records every method call, in order, with its arguments.

    Return shapes follow the real Runner's public methods:
    pre_send_once() -> (ok, retryable, record); send() -> bool;
    verify() -> bool.
    """

    def __init__(self, header_ok):
        self.header_ok = header_ok
        self.calls = []  # list of (method_name, args, kwargs)

    def _record(self, name, args=(), kwargs=None):
        self.calls.append((name, args, kwargs or {}))

    def called(self, name):
        return [c for c in self.calls if c[0] == name]

    def call_names(self):
        return [c[0] for c in self.calls]

    def check_recipient_header(self):
        self._record("check_recipient_header")
        return self.header_ok

    def pre_send_once(self, text):
        self._record("pre_send_once", (text,))
        return (True, False, {"fake": "pre_send"})

    def send(self):
        self._record("send")
        return True

    def verify(self, text, mode, fetch_png=False):
        self._record("verify", (text, mode), {"fetch_png": fetch_png})
        return True

    def cleanup_wrong_recipient(self):
        self._record("cleanup_wrong_recipient")
        return True

    def clear(self):
        self._record("clear")
        return True


def test_h_run_post_wrong_recipient_blocks_before_pre_send():
    runner = FakeRunner(header_ok=False)

    ok, error, info = gui_flow.run_post(runner, LINE_TEXT)

    assert ok is False
    assert error == "wrong_recipient"
    assert info == {"count": 0, "attempts": []}
    # Header was checked exactly once, and nothing else was touched.
    assert len(runner.called("check_recipient_header")) == 1
    assert runner.called("pre_send_once") == []
    assert runner.called("send") == []
    assert runner.called("cleanup_wrong_recipient") == []


def test_i_run_post_header_ok_proceeds_and_checks_header_first():
    runner = FakeRunner(header_ok=True)

    ok, error, info = gui_flow.run_post(runner, LINE_TEXT)

    assert ok is True
    names = runner.call_names()
    assert len(runner.called("check_recipient_header")) == 1
    assert names.index("check_recipient_header") < names.index(
        "pre_send_once"
    )
    # Successful flow: send, then post-send bubble verification.
    assert len(runner.called("send")) == 1
    verify_calls = runner.called("verify")
    assert len(verify_calls) == 1
    _, v_args, v_kwargs = verify_calls[0]
    assert v_args == (LINE_TEXT, "bubble")
    assert v_kwargs.get("fetch_png") is True
    assert names.index("send") < names.index("verify")
