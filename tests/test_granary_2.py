"""Behavior tests for the camera-free PLAF105 adapter; no cloud calls."""

import importlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.petlibro import PLATFORMS_BY_TYPE
from custom_components.petlibro.api import PetLibroAPI
from custom_components.petlibro.button import DEVICE_BUTTON_MAP, PetLibroButtonEntity
from custom_components.petlibro.const import Unit
from custom_components.petlibro.devices import get_device_class
from custom_components.petlibro.devices.feeders.granary_2_feeder import Granary2Feeder
from custom_components.petlibro.devices.feeders.granary_smart_camera_feeder import GranarySmartCameraFeeder
from custom_components.petlibro.devices.feeders.granary_smart_feeder import GranarySmartFeeder
from custom_components.petlibro.exceptions import PetLibroAPIError
from custom_components.petlibro.hub import PetLibroHub
from custom_components.petlibro.sensor import DEVICE_SENSOR_MAP, PetLibroSensorEntity


@pytest_asyncio.fixture
async def feeder(tmp_path):
    """Create a device with synthetic sibling-model response shapes."""
    hass = HomeAssistant(str(tmp_path))
    api = Mock(spec=PetLibroAPI)
    api.time_zone = "America/Chicago"
    api.get_device_meal_records.return_value = []
    for method in (
        "device_base_info", "device_attribute_settings", "get_device_upgrade",
        "get_default_matrix", "device_feeding_plan_today_new", "device_grain_status",
    ):
        getattr(api, method).return_value = {}
    api.device_real_info.return_value = {"online": True, "surplusGrain": True}
    api.device_get_bound_pets.return_value = []
    api.get_device_work_record.return_value = []
    api.device_feeding_plan_list.return_value = []
    api.device_data_real_info.return_value = {
        "remainingGrain": 16, "maxFeedable": 3, "portionToGramRatio": 8,
        "feedingMode": "PLAN",
    }
    api.device_get_free_feeding_setting.return_value = {
        "freePerGrainNum": 1, "freeDailyMaxNum": 13,
        "freeLeftoverWeight": 3, "freeWaitSeconds": 3,
    }
    hub = SimpleNamespace(
        api=api, member=SimpleNamespace(feedUnitType=Unit.CUPS),
        coordinator=DataUpdateCoordinator(hass, logging.getLogger(__name__), name="test", config_entry=None),
    )
    hub.coordinator.last_update_success = True
    device = Granary2Feeder({
        "deviceSn": "test-feeder", "productIdentifier": "PLAF105",
        "productName": "Granary 2", "name": "Test feeder", "device_id": "test-id",
        "enableFeedingPlan": True,
    }, hub)
    device.saved_to_options = True
    return device


@pytest.mark.parametrize("data,expected", [
    ({"productIdentifier": "PLAF105", "productName": "Localized name"}, Granary2Feeder),
    ({"productIdentifier": " plaf105 ", "productName": "Granary Smart Feeder"}, Granary2Feeder),
    ({"productIdentifier": "PLAF105"}, Granary2Feeder),
    ({"productName": "Granary 2"}, Granary2Feeder),
    ({"productIdentifier": "PLAF103", "productName": "Granary Smart Feeder"}, GranarySmartFeeder),
    ({"productName": "Granary Smart Camera Feeder"}, GranarySmartCameraFeeder),
    ({"productIdentifier": "PLAF205", "productName": "Granary 2 Vision"}, None),
    ({"productName": "Unknown feeder"}, None),
])
def test_hardware_discovery_and_legacy_devices(data, expected):
    assert get_device_class(data) is expected


@pytest.mark.asyncio
async def test_hub_loads_localized_plaf105(feeder):
    hub = PetLibroHub.__new__(PetLibroHub)
    hub.api = feeder.api
    hub.member = feeder.member
    hub.devices = {}
    hub.last_refresh_times = {}
    hub.entry = SimpleNamespace(options={"enable_shared_pets": False})
    hub.devices_helper = SimpleNamespace(
        loaded_device_sn=set(), remove_device_entries=AsyncMock(),
    )
    hub.api.list_devices.return_value = [{
        "deviceSn": "test-feeder", "productIdentifier": "PLAF105",
        "productName": "Localized name",
    }]
    await hub.load_devices()
    assert isinstance(hub.devices["test-feeder"], Granary2Feeder)
    assert hub.devices_helper.loaded_device_sn == {"test-feeder"}
    hub.devices_helper.remove_device_entries.assert_awaited_once()


def test_reuses_feeder_entities_without_camera_or_duplicate_keys(feeder):
    platforms = {p for cls, values in PLATFORMS_BY_TYPE.items()
                 if isinstance(feeder, cls) for p in values}
    assert {"button", "number", "select", "sensor", "binary_sensor", "update"} <= platforms
    assert "camera" not in platforms
    assert not isinstance(feeder, GranarySmartCameraFeeder)
    for platform in platforms:
        module = importlib.import_module(f"custom_components.petlibro.{platform}")
        descriptions = getattr(module, f"DEVICE_{platform.upper()}_MAP")
        keys = [d.key for cls, values in descriptions.items()
                if isinstance(feeder, cls) for d in values]
        assert len(keys) == len(set(keys)), platform
        assert not any("camera" in key or "night_vision" in key or "detection" in key for key in keys)
    keys = {d.key for cls, values in DEVICE_BUTTON_MAP.items()
            if isinstance(feeder, cls) for d in values}
    assert {"manual_feed", "enable_feeding_plan", "free_feeding_mode", "plan_feeding_mode"} <= keys


@pytest.mark.asyncio
async def test_refresh_reads_scale_and_refill_without_camera_calls(feeder):
    await feeder.refresh()
    assert feeder.available
    assert feeder.remaining_food_weight == 16
    assert feeder.max_feedable == 3
    assert feeder.portion_to_gram_ratio == 8
    assert feeder.feeding_mode == "plan"
    assert feeder.free_feeding_per_grain == 1
    assert feeder.free_feeding_daily_max == 13
    assert feeder.free_feeding_leftover_weight == 3
    assert feeder.free_feeding_wait_seconds == 3
    assert not any("tutk" in call[0] or "camera" in call[0] for call in feeder.api.mock_calls)
    feeder.api.set_manual_feed.assert_not_called()


@pytest.mark.asyncio
async def test_inherited_manual_feed_uses_selected_portions(feeder):
    await feeder.set_manual_feed_quantity(2)
    await feeder.set_manual_feed()
    feeder.api.set_manual_feed.assert_awaited_once_with("test-feeder", 2)
    feeder.api.device_data_real_info.assert_awaited_once_with("test-feeder")


@pytest.mark.parametrize("value,expected", [
    (0, 0), (12.5, 12.5), (None, None), (True, None), (False, None),
    ("12", None), (-1, None), (float("nan"), None), (float("inf"), None),
])
def test_weight_preserves_zero_and_rejects_invalid_values(feeder, value, expected):
    feeder.update_data({"dataRealInfo": {"remainingGrain": value}})
    assert feeder.remaining_food_weight == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_method,stale_key", [
    ("device_data_real_info", "dataRealInfo"),
    ("device_get_free_feeding_setting", "freeFeedingSetting"),
])
@pytest.mark.parametrize("error", [PetLibroAPIError("unsupported"), aiohttp.ClientError("offline")])
async def test_failed_optional_poll_clears_stale_values_and_keeps_other_data(
    feeder, failing_method, stale_key, error,
):
    await feeder.refresh()
    getattr(feeder.api, failing_method).side_effect = error
    await feeder.refresh()
    assert feeder._data[stale_key] == {}
    assert feeder.online
    assert feeder.food_low is False
    if stale_key == "dataRealInfo":
        assert feeder.remaining_food_weight is None
        assert feeder.feeding_mode is None
        assert feeder.free_feeding_daily_max == 13
    else:
        assert feeder.free_feeding_daily_max is None
        assert feeder.remaining_food_weight == 16


@pytest.mark.asyncio
async def test_ha_sensor_becomes_unavailable_and_recovers_with_valid_zero(feeder):
    description = next(d for d in DEVICE_SENSOR_MAP[Granary2Feeder] if d.key == "remaining_food_weight")
    sensor = PetLibroSensorEntity(feeder, feeder.hub, description)
    await feeder.refresh()
    assert sensor.available and sensor.native_value == 16
    feeder.api.device_data_real_info.side_effect = PetLibroAPIError("offline")
    await feeder.refresh()
    assert not sensor.available and sensor.native_value is None
    feeder.api.device_data_real_info.side_effect = None
    feeder.api.device_data_real_info.return_value = {"remainingGrain": 0}
    await feeder.refresh()
    assert sensor.available and sensor.native_value == 0


@pytest.mark.parametrize("info,expected", [
    ({}, None), ({"surplusGrain": True}, False), ({"surplusGrain": False}, True),
    ({"warehouseSurplusGrain": "GOOD"}, False), ({"warehouseSurplusGrain": "UNKNOWN"}, None),
])
def test_missing_food_status_does_not_report_empty_tank(feeder, info, expected):
    feeder.update_data({"realInfo": info})
    assert feeder.food_low is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("key,mode", [("free_feeding_mode", "FREE"), ("plan_feeding_mode", "PLAN")])
async def test_ha_mode_buttons_send_mode_and_refresh(feeder, key, mode):
    description = next(d for d in DEVICE_BUTTON_MAP[Granary2Feeder] if d.key == key)
    button = PetLibroButtonEntity(feeder, feeder.hub, description)
    await button.async_press()
    feeder.api.set_feeding_mode.assert_awaited_once_with("test-feeder", mode)
    feeder.api.device_data_real_info.assert_awaited_once_with("test-feeder")


@pytest.mark.asyncio
async def test_button_propagates_api_failure(feeder):
    feeder.api.set_feeding_mode.side_effect = PetLibroAPIError("rejected")
    button = PetLibroButtonEntity(feeder, feeder.hub, DEVICE_BUTTON_MAP[Granary2Feeder][0])
    with pytest.raises(PetLibroAPIError, match="rejected"):
        await button.async_press()
    feeder.api.device_data_real_info.assert_not_called()


@pytest.mark.asyncio
async def test_api_mode_payload_and_validation():
    api = PetLibroAPI.__new__(PetLibroAPI)
    api.session = SimpleNamespace(post=AsyncMock(), post_serial=AsyncMock())
    for mode in ("FREE", "PLAN"):
        await api.set_feeding_mode("test-feeder", mode)
        api.session.post.assert_awaited_with(
            "/device/device/updateFeedingMode", json={"deviceSn": "test-feeder", "mode": mode},
        )
    with pytest.raises(ValueError):
        await api.set_feeding_mode("test-feeder", "invalid")
    assert api.session.post.await_count == 2
    await api.device_get_free_feeding_setting("test-feeder")
    api.session.post_serial.assert_awaited_once_with(
        "/device/device/getFreeFeedingSetting", "test-feeder",
    )


@pytest.mark.asyncio
async def test_mode_enum_matches_localized_states(feeder):
    await feeder.refresh()
    description = next(d for d in DEVICE_SENSOR_MAP[Granary2Feeder] if d.key == "feeding_mode")
    sensor = PetLibroSensorEntity(feeder, feeder.hub, description)
    assert sensor.native_value == "plan"
    assert sensor.native_value in description.options
    root = Path(__file__).resolve().parents[1] / "custom_components/petlibro"
    for filename in ("strings.json", "translations/en.json"):
        labels = json.loads((root / filename).read_text())["entity"]["sensor"]["feeding_mode"]["state"]
        assert set(labels) == set(description.options)
        assert all(key == key.lower() for key in labels)


@pytest.mark.asyncio
async def test_consumption_matches_measured_intake_without_counting_pet_copy(feeder, monkeypatch):
    module = importlib.import_module(Granary2Feeder.__module__)
    monkeypatch.setattr(module, "utcnow", lambda: datetime(2026, 10, 10, 12, tzinfo=timezone.utc))
    feeder.api.get_device_meal_records.return_value = [
        {"intake": 3, "petsInfo": [{"intake": 3}], "params": '{"intake":3}'},
        {"intake": 2.5},
    ]
    await feeder.refresh()
    description = next(d for d in DEVICE_SENSOR_MAP[Granary2Feeder] if d.key == "today_consumed_food_weight")
    sensor = PetLibroSensorEntity(feeder, feeder.hub, description)
    assert sensor.available and sensor.native_value == 5.5
    assert description.native_unit_of_measurement == "g"
    assert description.state_class == "total"
    assert sensor.last_reset.isoformat() == "2026-10-10T00:00:00-05:00"
    feeder.api.get_device_meal_records.assert_awaited_once_with(
        "test-feeder", int(sensor.last_reset.timestamp() * 1000),
        int(datetime(2026, 10, 10, 12, tzinfo=timezone.utc).timestamp() * 1000),
    )
    feeder.api.set_manual_feed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, "3", -1, float("nan"), float("inf")])
async def test_invalid_intake_clears_total_instead_of_counting_as_zero(feeder, value):
    feeder.api.get_device_meal_records.return_value = [{"intake": 3}]
    await feeder.refresh()
    assert feeder.today_consumed_food_weight == 3
    feeder.api.get_device_meal_records.return_value = [{"intake": value}]
    await feeder.refresh()
    assert feeder.today_consumed_food_weight is None
    assert feeder.consumption_last_reset is None
    assert feeder.remaining_food_weight == 16


@pytest.mark.asyncio
async def test_consumption_failure_recovery_and_dst_midnight(feeder, monkeypatch):
    module = importlib.import_module(Granary2Feeder.__module__)
    clock = [datetime(2026, 11, 1, 6, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(module, "utcnow", lambda: clock[0])
    feeder.api.get_device_meal_records.return_value = [{"intake": 3}]
    await feeder.refresh()
    first_reset = feeder.consumption_last_reset
    assert first_reset.isoformat() == "2026-11-01T00:00:00-05:00"
    clock[0] = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
    await feeder.refresh()
    assert feeder.consumption_last_reset == first_reset
    feeder.api.get_device_meal_records.side_effect = PetLibroAPIError("incomplete history")
    await feeder.refresh()
    assert feeder.today_consumed_food_weight is None
    feeder.api.get_device_meal_records.side_effect = None
    feeder.api.get_device_meal_records.return_value = []
    clock[0] = datetime(2026, 11, 2, 6, 1, tzinfo=timezone.utc)
    await feeder.refresh()
    assert feeder.today_consumed_food_weight == 0
    assert feeder.consumption_last_reset.isoformat() == "2026-11-02T00:00:00-06:00"


def meal_history_api(*pages):
    api = PetLibroAPI.__new__(PetLibroAPI)
    api.session = SimpleNamespace(request=AsyncMock(side_effect=pages))
    return api


def history_page(records):
    return [{"recordTime": "2026/10/10", "workRecords": records}]


@pytest.mark.asyncio
async def test_daily_history_handles_overlap_ignored_type_filter_and_date_range():
    records = [
        {"id": f"test-{i}", "type": "MEAL_RECORD", "recordTime": 2000 - i, "intake": 1}
        for i in range(25)
    ]
    api = meal_history_api(history_page(records), history_page([
        records[-1],
        {"id": "online", "type": "ONLINE", "recordTime": 1900},
        {"id": "another-meal", "type": "MEAL_RECORD", "recordTime": 1800, "intake": 3},
        {"id": "yesterday", "type": "MEAL_RECORD", "recordTime": 900, "intake": 20},
    ]))
    result = await api.get_device_meal_records("test-feeder", 1000, 3000)
    assert len(result) == 26
    assert sum(r["intake"] for r in result) == 28
    assert api.session.request.call_args_list[0].kwargs["json"] == {
        "deviceSn": "test-feeder", "startTime": 1000, "endTime": 3000,
        "size": 25, "type": ["MEAL_RECORD"],
    }
    assert api.session.request.call_args_list[1].kwargs["json"]["endTime"] == 1976


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [[], {}])
async def test_empty_history_is_valid_zero_meals(page):
    assert await meal_history_api(page).get_device_meal_records("test", 1000, 3000) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [
    {"unexpected": []}, [None], [{}], history_page([None]),
    history_page([{"type": "MEAL_RECORD", "recordTime": True}]),
    history_page([{"type": "MEAL_RECORD", "recordTime": 2000}]),
])
async def test_malformed_history_cannot_silently_undercount(page):
    with pytest.raises(PetLibroAPIError):
        await meal_history_api(page).get_device_meal_records("test", 1000, 3000)


@pytest.mark.asyncio
async def test_pagination_ignoring_cursor_fails_instead_of_returning_partial_total():
    records = [{"id": f"r{i}", "type": "MEAL_RECORD", "recordTime": 2000 - i} for i in range(25)]
    api = meal_history_api(history_page(records), history_page(records))
    with pytest.raises(PetLibroAPIError, match="did not advance"):
        await api.get_device_meal_records("test", 1000, 3000)


@pytest.mark.asyncio
async def test_daily_history_page_limit_does_not_return_partial_total():
    pages = [history_page([
        {"id": f"r{p}-{i}", "type": "MEAL_RECORD", "recordTime": 2000 - p * 25 - i}
        for i in range(25)
    ]) for p in range(10)]
    api = meal_history_api(*pages)
    with pytest.raises(PetLibroAPIError, match="page limit"):
        await api.get_device_meal_records("test", 1000, 3000)
    assert api.session.request.await_count == 10
