"""Headless smoke test of the Streamlit UI (renders, simulates, shows metrics, offers the workbook)."""

from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"


def test_app_renders_with_default_preset():
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Annualized loss expectancy" in labels and "Readiness score" in labels
    assert any("Knock-out" in e.value for e in at.error)  # dental preset has knock-out gaps
