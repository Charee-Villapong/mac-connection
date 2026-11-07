"""仕様テスト: ヘッダ判定クロップの名前帯分離 (header_verify_crop_max_y).

実装コード (gui_flow.py) を読まず、仕様書
(~/workspace/gui-flow-header-crop-fix/DEFINITION.md) だけから書いた
受け入れテスト。既存の spec テスト (test_header_crop_spec.py) の
流儀 (build_verify_script の呼び出し形式の試し方、素の acfg の形)
に倣う。

担保する仕様 (DEFINITION.md「変更の内容」1・2 と「受け入れ条件」1):
  build_verify_script のクロップ高さ (生成スクリプト内の awk 引数
  `-v max_y=<値>`) の解決に、新キー `header_verify_crop_max_y` を
  最優先で追加する。解決順は厳密に:
    acfg["header_verify_crop_max_y"]
    → tun["header_verify_crop_max_y"]
    → acfg["relearn_header_max_y"]
    → tun["relearn_header_max_y"]
    → DEFAULT_TUNABLES["relearn_header_max_y"] (260)
  - 新キーがどこにも無い場合、生成スクリプトは現行と完全に同一
    (max_y リテラルも従来の解決結果と同じ)。
  - 値の整形は既存の _script_number を使う (260.0 → "260" 等)。
  - DEFAULT_TUNABLES には新キーを足さない (未設定 = 現行挙動が正)。

値はいずれの既定値・座標とも衝突しない特徴的な数値を使い、
max_y リテラルとして現れる数値だけを抜き出して比較する。

実行: python3 -m pytest tests/test_header_verify_crop_spec.py -q
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gui_flow  # noqa: E402

from spec_fixtures import make_bare_discord_acfg  # noqa: E402
from spec_fixtures import make_bare_line_acfg_verify_crop  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

NEW_KEY = "header_verify_crop_max_y"
RELEARN_KEY = "relearn_header_max_y"
DEFAULT_MAX_Y = "260"

# 解決順テスト用の特徴的な値 (他のリテラルと衝突しないもの)
ACFG_NEW = 111
TUN_NEW = 222
ACFG_RELEARN = 233
TUN_RELEARN = 244

# build_verify_script 用の素の acfg (test_header_crop_spec.py の
# BARE_ACFG と同形)
BARE_DISCORD_ACFG = make_bare_discord_acfg()

BARE_LINE_ACFG = make_bare_line_acfg_verify_crop()

_MAX_Y_RE = re.compile(r"max_y=(\d+(?:\.\d+)?)")


def max_y_values(script):
    """生成スクリプト内の max_y リテラル (数値文字列) の集合。"""
    return set(_MAX_Y_RE.findall(script))


def all_max_y_values(scripts):
    values = set()
    for s in scripts:
        values |= max_y_values(s)
    return values


def _script_attempts(acfg, tun_overrides=None, app="discord"):
    """既存 spec テストと同じく、値の渡し方 (acfg 直下 / tunables
    経由の複数形式) を試し、生成できたスクリプトを全部返す。"""
    scripts = []
    # 基本形式 (3 引数) は失敗したらそのまま伝播させる
    scripts.append(gui_flow.build_verify_script("gf-x.sh", app, acfg))
    if tun_overrides is not None:
        tun_variants = []
        try:
            tun_variants.append(gui_flow.tunables({"tunables": tun_overrides}))
        except Exception:
            pass
        tun_variants.append(dict(tun_overrides))
        for tun in tun_variants:
            for call in (
                lambda tun=tun: gui_flow.build_verify_script(
                    "gf-x.sh", app, acfg, tun
                ),
                lambda tun=tun: gui_flow.build_verify_script(
                    "gf-x.sh", app, acfg, tun=tun
                ),
            ):
                try:
                    scripts.append(call())
                except Exception:
                    pass
    return scripts


def _acfg(base, extra):
    acfg = dict(base)
    if extra:
        acfg.update(extra)
    return acfg


# ---------------------------------------------------------------------------
# 解決順: 新キーの優先 (受け入れ条件 1 の 1・2 番目)
# ---------------------------------------------------------------------------


def test_acfg_new_key_beats_tun_new_key_and_both_relearns():
    """仕様: 解決順は acfg の新キーが最優先
    (「acfg["header_verify_crop_max_y"] → tun[...] → ...」)。
    受け入れ条件 1: 「acfg の新キーが tun の新キーより優先される」。

    4 つの候補をすべて違う値で置き、勝者 (acfg の新キー) だけが
    max_y リテラルに現れ、敗者 (tun の新キー / 両 relearn) が
    どの生成形式でも現れないことを見る。
    """
    acfg = _acfg(
        BARE_DISCORD_ACFG,
        {NEW_KEY: ACFG_NEW, RELEARN_KEY: ACFG_RELEARN},
    )
    tun = {NEW_KEY: TUN_NEW, RELEARN_KEY: TUN_RELEARN}

    scripts = _script_attempts(acfg, tun_overrides=tun)

    values = all_max_y_values(scripts)
    assert str(ACFG_NEW) in values, f"max_y に acfg の新キーが無い: {values!r}"
    losers = {str(TUN_NEW), str(ACFG_RELEARN), str(TUN_RELEARN)}
    assert not (losers & values), f"敗者の値が max_y に現れた: {values!r}"


def test_tun_new_key_beats_acfg_relearn():
    """仕様: 解決順で tun の新キーは acfg の relearn より前
    (「→ tun["header_verify_crop_max_y"] →
    acfg["relearn_header_max_y"] → ...」)。
    受け入れ条件 1: 「tun の新キーが acfg の
    relearn_header_max_y より優先される」。

    acfg に新キーが無い状態で tun 経由の生成に新キーの値が勝者と
    して現れることを見る (基本形式では tun を渡さないため、
    tun 経由のいずれかのスクリプトに勝者が現れればよい)。
    """
    acfg = _acfg(BARE_DISCORD_ACFG, {RELEARN_KEY: ACFG_RELEARN})
    tun = {NEW_KEY: TUN_NEW, RELEARN_KEY: TUN_RELEARN}

    scripts = _script_attempts(acfg, tun_overrides=tun)

    assert any(
        max_y_values(s) == {str(TUN_NEW)} for s in scripts
    ), f"tun の新キーが勝者になったスクリプトが無い: {scripts!r}"


def test_tun_new_key_alone_resolves():
    """仕様: 解決順の 2 番目 (tun の新キー) は、acfg 側に新キーも
    relearn も無い場合にも効く (受け入れ条件 1 の解決順の担保)。

    tun 経由のいずれかのスクリプトで max_y が新キーの値になる
    ことを見る。
    """
    acfg = _acfg(BARE_DISCORD_ACFG, None)
    tun = {NEW_KEY: TUN_NEW}

    scripts = _script_attempts(acfg, tun_overrides=tun)

    assert any(
        max_y_values(s) == {str(TUN_NEW)} for s in scripts
    ), f"tun の新キーだけで解決されたスクリプトが無い: {scripts!r}"


# ---------------------------------------------------------------------------
# 解決順: 新キーが無い場合の従来解決 (受け入れ条件 1 の 3 番目)
# ---------------------------------------------------------------------------


def test_acfg_relearn_beats_tun_relearn_without_new_key():
    """仕様: 「新キーが無い場合、従来の解決 (acfg relearn →
    tun relearn → 260) がそのまま効く」
    (受け入れ条件 1 の 3 番目)。

    acfg の relearn が tun の relearn より優先され、tun の値が
    max_y リテラルに現れないことを見る。新キー未実装の現行でも
    通るはずの従来挙動。
    """
    acfg = _acfg(BARE_DISCORD_ACFG, {RELEARN_KEY: ACFG_RELEARN})
    tun = {RELEARN_KEY: TUN_RELEARN}

    scripts = _script_attempts(acfg, tun_overrides=tun)

    values = all_max_y_values(scripts)
    assert str(ACFG_RELEARN) in values, f"acfg relearn が無い: {values!r}"
    assert str(TUN_RELEARN) not in values, f"tun relearn が漏れた: {values!r}"


def test_tun_relearn_used_when_acfg_has_none_without_new_key():
    """仕様: 「新キーが無い場合、従来の解決 (acfg relearn →
    tun relearn → 260) がそのまま効く」
    (受け入れ条件 1 の 3 番目)。

    acfg に relearn が無い場合は tun の relearn が効く。新キー
    未実装の現行でも通るはずの従来挙動。
    """
    acfg = _acfg(BARE_DISCORD_ACFG, None)
    tun = {RELEARN_KEY: TUN_RELEARN}

    scripts = _script_attempts(acfg, tun_overrides=tun)

    assert any(
        max_y_values(s) == {str(TUN_RELEARN)} for s in scripts
    ), f"tun relearn で解決されたスクリプトが無い: {scripts!r}"


def test_default_260_when_nothing_set():
    """仕様: 解決順の末尾は DEFAULT_TUNABLES の
    relearn_header_max_y (260) で、「新キーが無い場合、生成
    スクリプトは現行と完全に同一 (max_y リテラルも従来の解決
    結果と同じ)」(受け入れ条件 1 の 3 番目)。

    何も設定しなければ max_y リテラルは 260 のみになる。
    新キー未実装の現行でも通るはずの従来挙動。
    """
    scripts = _script_attempts(_acfg(BARE_DISCORD_ACFG, None))

    assert scripts
    assert all_max_y_values(scripts) == {DEFAULT_MAX_Y}


def test_discord_acfg_relearn_override_without_new_key():
    """仕様: 「新キーが無い場合、従来の解決がそのまま効く」+
    受け入れ条件 1 の 5 番目: 「discord 相当の acfg (新キーなし)
    では max_y リテラルが従来通り 260 (または relearn 上書き値)
    になる」。

    Discord の挙動は変更しない (新キー未設定のため現行通り)。
    relearn 上書き値がそのまま max_y になることを見る。
    """
    acfg = _acfg(BARE_DISCORD_ACFG, {RELEARN_KEY: 231})

    scripts = _script_attempts(acfg)

    assert all_max_y_values(scripts) == {"231"}


# ---------------------------------------------------------------------------
# アプリ別: line は新キー 100、discord は従来通り
# (受け入れ条件 1 の 4・5 番目)
# ---------------------------------------------------------------------------


def test_line_acfg_new_key_100_becomes_max_y_literal():
    """仕様: 受け入れ条件 1 の 4 番目: 「line 相当の acfg
    (新キー 100) で生成したスクリプトの max_y リテラルが
    100 になる」。あわせて、仕様が定める生成スクリプト内の
    形 (awk 引数 `-v max_y=<値>`) で現れることも見る。

    新キーが最優先なので、既定の 260 は現れない。
    """
    acfg = _acfg(BARE_LINE_ACFG, {NEW_KEY: 100})

    scripts = _script_attempts(acfg, app="line")

    values = all_max_y_values(scripts)
    assert "100" in values, f"max_y に 100 が無い: {values!r}"
    assert DEFAULT_MAX_Y not in values, f"既定 260 が残った: {values!r}"
    assert any(
        "-v max_y=100" in s for s in scripts
    ), "awk 引数 `-v max_y=100` の形で現れるスクリプトが無い"


def test_discord_acfg_without_new_key_stays_default_260():
    """仕様: 受け入れ条件 1 の 5 番目: 「discord 相当の acfg
    (新キーなし) では max_y リテラルが従来通り 260 になる」。
    「Discord の挙動は変更しない (新キー未設定のため現行通りに
    なること)」。新キー未実装の現行でも通るはずの従来挙動。
    """
    scripts = _script_attempts(_acfg(BARE_DISCORD_ACFG, None), app="discord")

    assert scripts
    assert all_max_y_values(scripts) == {DEFAULT_MAX_Y}


# ---------------------------------------------------------------------------
# 値の整形 (変更の内容 1: _script_number を使う)
# ---------------------------------------------------------------------------


def test_new_key_float_value_formatted_like_script_number():
    """仕様: 「値の整形は既存の _script_number を使う
    (260.0 → "260" 等、現行と同じ表記)」。

    新キーに小数形 (100.0) を渡しても、max_y リテラルは
    整数表記 "100" になり、"100.0" にはならない。
    """
    acfg = _acfg(BARE_LINE_ACFG, {NEW_KEY: 100.0})

    scripts = _script_attempts(acfg, app="line")

    values = all_max_y_values(scripts)
    assert "100" in values, f"max_y に 100 が無い: {values!r}"
    assert "100.0" not in values, f"整形されていない値が現れた: {values!r}"


def test_relearn_float_value_formatted_like_script_number():
    """仕様: 「値の整形は既存の _script_number を使う
    (260.0 → "260" 等、現行と同じ表記)」の従来経路分。

    relearn に小数形 (231.0) を渡しても max_y リテラルは
    "231" になる。新キー未実装の現行でも通るはずの従来挙動。
    """
    acfg = _acfg(BARE_DISCORD_ACFG, {RELEARN_KEY: 231.0})

    scripts = _script_attempts(acfg)

    assert all_max_y_values(scripts) == {"231"}


# ---------------------------------------------------------------------------
# DEFAULT_TUNABLES には新キーを足さない (変更の内容 2)
# ---------------------------------------------------------------------------


def test_default_tunables_has_no_new_key():
    """仕様: 「DEFAULT_TUNABLES には新キーを足さない
    (未設定 = 現行挙動が正)」(変更の内容 2)。
    あわせて、解決順の末尾である relearn_header_max_y の
    既定が 260 のままであることも見る (仕様: 「
    DEFAULT_TUNABLES["relearn_header_max_y"] (260)」)。
    """
    assert NEW_KEY not in gui_flow.DEFAULT_TUNABLES
    assert gui_flow.DEFAULT_TUNABLES[RELEARN_KEY] == 260
