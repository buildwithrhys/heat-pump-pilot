from predheat.model import design_outdoor_c, survey_to_coefficient
from predheat.service.settings_store import FIELDS, SettingsStore, validate
from predheat.config import DataMode, Settings


def test_design_temperature_by_region():
    assert design_outdoor_c(51.48, -3.18) == (-3.0, "Wales")  # Cardiff
    assert design_outdoor_c(51.5, -0.12)[0] == -2.0  # London
    assert design_outdoor_c(57.1, -2.1)[0] == -4.5  # Aberdeen
    assert design_outdoor_c(None, None)[1] == "UK default"


def test_optional_settings_accept_empty_and_validate():
    assert validate("survey_heat_loss_kw", "") is None
    assert validate("survey_heat_loss_kw", 6.95) == 6.95
    assert FIELDS["heat_pump_rated_kw"]["optional"]


def test_survey_prior_flows_into_settings(tmp_path):
    st = SettingsStore.load(tmp_path)
    s = Settings(data_mode=DataMode.SIMULATED)
    st.update({"survey_heat_loss_kw": 6.95, "survey_outdoor_c": -3, "heat_pump_rated_kw": 7}, s)
    assert s.survey_heat_loss_kw == 6.95 and s.heat_pump_rated_kw == 7.0
    assert abs(survey_to_coefficient(6.95, 21.0, -3.0) - 289.6) < 0.1
