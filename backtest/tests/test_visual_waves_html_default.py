"""--visual-waves ma implicitne zapnout Plotly HTML export."""
from backtest.visual_waves import visual_params_from_combo_and_args


def test_cli_visual_waves_enables_html_without_visual_html_flag():
    *_, use_html, _ = visual_params_from_combo_and_args(
        {"wave_min_pct": 0.26},
        cli_visual_waves=True,
    )
    assert use_html is True


def test_profile_visual_waves_enabled_defaults_html():
    *_, use_html, _ = visual_params_from_combo_and_args(
        {"visual_waves_enabled": True},
    )
    assert use_html is True


def test_profile_can_disable_html_explicitly():
    *_, use_html, _ = visual_params_from_combo_and_args(
        {
            "visual_waves_enabled": True,
            "visual_waves_plotly_html": False,
        },
    )
    assert use_html is False


def test_cli_visual_waves_overrides_profile_html_off():
    *_, use_html, _ = visual_params_from_combo_and_args(
        {
            "visual_waves_enabled": True,
            "visual_waves_plotly_html": False,
        },
        cli_visual_waves=True,
    )
    assert use_html is True
