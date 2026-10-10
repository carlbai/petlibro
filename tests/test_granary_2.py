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


@pytest.mark.asyncio
async def test_meal_statistics_use_seconds_and_latest_timestamp(feeder):
    feeder.api.get_device_meal_records.return_value = [
        {"intake": 3, "recordTime": 2000000, "petEatingStartTime": 1800000,
         "eatingEndTime": 1954310, "duration": 2, "petsInfo": [{"intake": 3, "duration": 154}]},
        {"intake": 2, "recordTime": 3000000, "petEatingStartTime": 2900000, "eatingEndTime": 2931000},
    ]
    await feeder.refresh()
    assert feeder.today_meal_count == 2
    assert feeder.today_meal_duration == 185
    assert feeder.today_consumed_food_weight == 5
    assert feeder.last_meal_time.timestamp() == 3000
    for key in ("today_meal_count", "today_meal_duration"):
        description = next(d for d in DEVICE_SENSOR_MAP[Granary2Feeder] if d.key == key)
        entity = PetLibroSensorEntity(feeder, feeder.hub, description)
        assert entity.available and entity.last_reset == feeder.consumption_last_reset
    feeder.api.get_device_meal_records.side_effect = PetLibroAPIError("incomplete")
    await feeder.refresh()
    assert feeder.today_meal_count is None
    assert feeder.today_meal_duration is None
    assert feeder.last_meal_time is None


@pytest.mark.asyncio
async def test_zero_meals_retain_previous_day_last_meal(feeder):
    feeder.api.get_device_meal_records.side_effect = [[], [{"recordTime": 1000000}]]
    await feeder.refresh()
    assert feeder.today_meal_count == 0
    assert feeder.today_meal_duration == 0
    assert feeder.today_consumed_food_weight == 0
    assert feeder.last_meal_time.timestamp() == 1000
    assert feeder.api.get_device_meal_records.call_args.kwargs == {"latest_only": True}


@pytest.mark.asyncio
async def test_unknown_duration_does_not_erase_valid_consumption(feeder):
    feeder.api.get_device_meal_records.return_value = [{"intake": 3}]
    await feeder.refresh()
    assert feeder.today_meal_count == 1
    assert feeder.today_consumed_food_weight == 3
    assert feeder.today_meal_duration is None


@pytest.mark.asyncio
async def test_latest_meal_history_scans_non_meal_pages_then_stops():
    online = [{"id": f"online-{i}", "type": "ONLINE", "recordTime": 2900-i} for i in range(25)]
    api = meal_history_api(history_page(online), history_page([
        {"id": "older", "type": "MEAL_RECORD", "recordTime": 2000},
        {"id": "latest", "type": "MEAL_RECORD", "recordTime": 2500},
    ]))
    result = await api.get_device_meal_records("test", 1000, 3000, latest_only=True)
    assert [r["id"] for r in result] == ["latest"]
    assert api.session.request.await_count == 2


@pytest.mark.asyncio
async def test_calibration_changes_mass_estimates_and_keeps_volume(feeder):
    from collections import defaultdict
    feeder.hub.unit_entities = SimpleNamespace(unique_ids=defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    await feeder.refresh()
    feeder.update_data({"grainStatus": {"todayFeedingQuantity": 3}})
    description = next(d for d in DEVICE_SENSOR_MAP[GranarySmartFeeder] if d.key == "today_feeding_quantity_weight")
    sensor = PetLibroSensorEntity(feeder, feeder.hub, description)
    assert sensor.native_value == 24
    assert sensor.extra_state_attributes == {"g": 24, "oz": 0.85}
    assert feeder.feeding_amount(3, Unit.CUPS) == Unit.convert_feed(3, None, Unit.CUPS, True)
    assert feeder.portions_from_amount(24, Unit.GRAMS) == 3
    assert feeder.portions_from_amount(0.85, Unit.OUNCES) == 3
    assert feeder.feed_conv_factor == 1
    feeder.update_data({"dataRealInfo": {"portionToGramRatio": 0}})
    assert not sensor.available and sensor.native_value is None
    assert sensor.extra_state_attributes == {}
    with pytest.raises(PetLibroAPIError):
        feeder.portions_from_amount(24, Unit.GRAMS)
    # The legacy conversion constant remains unchanged for other feeder models.
    assert Unit.convert_feed(3, None, Unit.GRAMS, True) == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value,expected", [
    ("free_feeding_per_grain", 2, (2, 13)),
    ("free_feeding_daily_max", 6, (1, 6)),
])
async def test_refill_edits_read_fresh_companion_settings(feeder, key, value, expected):
    feeder.refresh = AsyncMock()
    feeder.update_data({"freeFeedingSetting": {"freePerGrainNum": 9, "freeDailyMaxNum": 99}})
    await feeder.set_free_feeding_setting(key, value)
    feeder.api.device_get_free_feeding_setting.assert_awaited_once_with("test-feeder")
    feeder.api.update_free_feeding_setting.assert_awaited_once_with("test-feeder", *expected)
    feeder.refresh.assert_awaited_once()
    feeder.api.set_manual_feed.assert_not_called()
    feeder.api.set_feeding_mode.assert_not_called()


@pytest.mark.asyncio
async def test_refill_threshold_is_independent_and_errors_propagate(feeder):
    from custom_components.petlibro.number import DEVICE_NUMBER_MAP, PetLibroNumberEntity
    feeder.refresh = AsyncMock()
    description = next(d for d in DEVICE_NUMBER_MAP[Granary2Feeder] if d.key == "free_feeding_leftover_weight")
    number = PetLibroNumberEntity(feeder, feeder.hub, description)
    number.async_write_ha_state = Mock()
    assert (number.native_min_value, number.native_max_value, number.native_unit_of_measurement) == (3, 10, "g")
    await number.async_set_native_value(3)
    feeder.api.update_free_feeding_threshold.assert_awaited_once_with("test-feeder", 3)
    feeder.api.device_get_free_feeding_setting.assert_not_called()
    feeder.api.update_free_feeding_threshold.side_effect = PetLibroAPIError("rejected")
    with pytest.raises(PetLibroAPIError, match="rejected"):
        await number.async_set_native_value(4)
    assert number.async_write_ha_state.call_count == 1


@pytest.mark.asyncio
async def test_simultaneous_refill_edits_preserve_both_values(feeder):
    import asyncio
    feeder.refresh = AsyncMock()
    settings = {"freePerGrainNum": 1, "freeDailyMaxNum": 6}
    async def read(_):
        await asyncio.sleep(0)
        return dict(settings)
    async def write(_, portions, daily_max):
        await asyncio.sleep(0)
        settings.update(freePerGrainNum=portions, freeDailyMaxNum=daily_max)
    feeder.api.device_get_free_feeding_setting.side_effect = read
    feeder.api.update_free_feeding_setting.side_effect = write
    await asyncio.gather(
        feeder.set_free_feeding_setting("free_feeding_per_grain", 2),
        feeder.set_free_feeding_setting("free_feeding_daily_max", 8),
    )
    assert settings == {"freePerGrainNum": 2, "freeDailyMaxNum": 8}


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value,expected", [
    ("meal_call_enabled", False, (False, 2, ["PLAN", "MANUAL"])),
    ("meal_call_times", 3, (True, 3, ["PLAN", "MANUAL"])),
    ("meal_call_free", True, (True, 2, ["PLAN", "MANUAL", "FREE"])),
    ("meal_call_plan", False, (True, 2, ["MANUAL"])),
    ("meal_call_manual", False, (True, 2, ["PLAN"])),
])
async def test_meal_call_edits_preserve_fresh_companion_values(feeder, key, value, expected):
    feeder.refresh = AsyncMock()
    feeder.api.device_data_real_info.return_value = {"mealCallSwitch": True, "mealCallTimes": 2, "mealCallApply": ["PLAN", "MANUAL"]}
    await feeder.set_meal_call_setting(key, value)
    feeder.api.update_meal_call_setting.assert_awaited_once_with("test-feeder", *expected)
    feeder.refresh.assert_awaited_once()
    feeder.api.set_manual_feed.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("settings", [None, {}, {"mealCallSwitch": True, "mealCallTimes": 2}])
async def test_unknown_meal_call_settings_cannot_be_overwritten(feeder, settings):
    feeder.api.device_data_real_info.return_value = settings
    with pytest.raises(PetLibroAPIError, match="cannot preserve"):
        await feeder.set_meal_call_setting("meal_call_enabled", False)
    feeder.api.update_meal_call_setting.assert_not_called()


@pytest.mark.asyncio
async def test_meal_call_switches_require_complete_settings(feeder):
    from custom_components.petlibro.switch import DEVICE_SWITCH_MAP, PetLibroSwitchEntity
    feeder.api.device_data_real_info.return_value = {"mealCallSwitch": False, "mealCallTimes": 2, "mealCallApply": ["PLAN"]}
    await feeder.refresh()
    description = next(d for d in DEVICE_SWITCH_MAP[Granary2Feeder] if d.key == "meal_call_enabled")
    switch = PetLibroSwitchEntity(feeder, feeder.hub, description)
    assert switch.available and switch.is_on is False
    feeder.refresh = AsyncMock()
    await switch.async_turn_on()
    feeder.api.update_meal_call_setting.assert_awaited_once_with("test-feeder", True, 2, ["PLAN"])
    feeder.update_data({"dataRealInfo": {"mealCallSwitch": False}})
    assert not switch.available


@pytest.mark.asyncio
async def test_setting_api_payloads():
    api = PetLibroAPI.__new__(PetLibroAPI)
    api.session = SimpleNamespace(post=AsyncMock())
    await api.update_free_feeding_setting("test", 2, 6)
    api.session.post.assert_awaited_with("/device/device/updateFreeFeedingSetting", json={"deviceSn": "test", "freePerGrainNum": 2, "freeDailyMaxNum": 6})
    await api.update_free_feeding_threshold("test", 3)
    api.session.post.assert_awaited_with("/device/device/updateFreeFeedingParamSetting", json={"deviceSn": "test", "freeLeftoverWeight": 3})
    await api.update_meal_call_setting("test", False, 2, ["PLAN", "MANUAL", "PLAN"])
    api.session.post.assert_awaited_with("/device/setting/updateMealCallSetting", json={"deviceSn": "test", "mealCallSwitch": False, "mealCallTimes": 2, "mealCallApply": ["PLAN", "MANUAL"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("update_free_feeding_setting", (0, 6)), ("update_free_feeding_setting", (11, 6)),
    ("update_free_feeding_setting", (True, 6)), ("update_free_feeding_setting", (1, 101)),
    ("update_free_feeding_setting", (1, None)), ("update_free_feeding_threshold", (2,)),
    ("update_free_feeding_threshold", (11,)), ("update_free_feeding_threshold", (3.5,)),
    ("update_meal_call_setting", ("true", 2, [])), ("update_meal_call_setting", (True, 0, [])),
    ("update_meal_call_setting", (True, 6, [])), ("update_meal_call_setting", (True, 2, ["UNKNOWN"])),
])
async def test_invalid_setting_payloads_never_post(method, args):
    api = PetLibroAPI.__new__(PetLibroAPI)
    api.session = SimpleNamespace(post=AsyncMock())
    with pytest.raises(ValueError):
        await getattr(api, method)("test", *args)
    api.session.post.assert_not_called()


@pytest.mark.asyncio
async def test_single_bowl_cleanup_and_read_only_status(feeder):
    from custom_components.petlibro.binary_sensor import DEVICE_BINARY_SENSOR_MAP, PetLibroBinarySensorEntity
    await feeder.refresh()
    for key in ("left_food_low", "right_food_low"):
        description = next(d for d in DEVICE_BINARY_SENSOR_MAP[GranarySmartFeeder] if d.key == key)
        entity = PetLibroBinarySensorEntity(feeder, feeder.hub, description)
        assert not entity.available and not entity.entity_registry_enabled_default
    description = next(d for d in DEVICE_SENSOR_MAP[GranarySmartFeeder] if d.key == "child_lock_switch")
    entity = PetLibroSensorEntity(feeder, feeder.hub, description)
    assert not entity.available and not entity.entity_registry_enabled_default
    feeder.update_data({"getAttributeSetting": {"childLockSwitch": False}})
    assert entity.available and entity.entity_registry_enabled_default
    feeder.update_data({"dataRealInfo": {"autoStopFeedSwitch": False, "autoFeedMaxWeight": 60}})
    assert feeder.auto_stop_feed_enabled is False
    assert feeder.auto_feed_max_weight == 60
    feeder.update_data({"dataRealInfo": {}})
    assert feeder.auto_stop_feed_enabled is None
    assert feeder.auto_feed_max_weight is None


@pytest.mark.asyncio
async def test_manual_mass_control_uses_calibration_without_dispensing(feeder):
    from homeassistant.const import Platform
    from custom_components.petlibro.number import DEVICE_NUMBER_MAP, PetLibroNumberEntity
    feeder.hub.entry = SimpleNamespace(options={})
    feeder.hub.unit_entities = SimpleNamespace(feed_number_unique_ids={Platform.NUMBER: []})
    feeder.member.feedUnitType = Unit.GRAMS
    await feeder.refresh()
    description = next(d for d in DEVICE_NUMBER_MAP[GranarySmartFeeder] if d.key == "manual_feed_quantity")
    entity = PetLibroNumberEntity(feeder, feeder.hub, description)
    entity.async_write_ha_state = Mock()
    assert entity.native_step == 8
    assert entity.native_min_value == 8
    assert entity.native_max_value == 8 * feeder.max_feed_portions
    assert entity.native_unit_of_measurement == "g"
    await entity.async_set_native_value(24)
    assert feeder.manual_feed_quantity == 3
    assert entity.native_value == 24
    feeder.api.set_manual_feed.assert_not_called()
    feeder.update_data({"dataRealInfo": {}})
    assert not entity.available and entity.native_value is None
    assert entity.native_min_value > 0 and entity.native_max_value > 0


def test_new_entities_have_english_translations(feeder):
    from custom_components.petlibro.number import DEVICE_NUMBER_MAP
    from custom_components.petlibro.switch import DEVICE_SWITCH_MAP
    from custom_components.petlibro.binary_sensor import DEVICE_BINARY_SENSOR_MAP
    maps = {"sensor": DEVICE_SENSOR_MAP, "number": DEVICE_NUMBER_MAP,
            "switch": DEVICE_SWITCH_MAP, "binary_sensor": DEVICE_BINARY_SENSOR_MAP}
    root = Path(__file__).resolve().parents[1] / "custom_components/petlibro"
    for filename in ("strings.json", "translations/en.json"):
        strings = json.loads((root / filename).read_text())["entity"]
        for platform, descriptions in maps.items():
            for description in descriptions[Granary2Feeder]:
                assert strings[platform][description.translation_key]["name"]
