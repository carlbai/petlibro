"""Camera-free Granary 2 (PLAF105) support."""

from logging import getLogger
from math import isfinite
from datetime import datetime, timedelta
from asyncio import Lock
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.util.dt import utcnow
from homeassistant.const import UnitOfMass
from homeassistant.util.unit_conversion import MassConverter

from ...exceptions import PetLibroAPIError
from ...const import Unit
from .granary_smart_feeder import GranarySmartFeeder

_LOGGER = getLogger(__name__)


def _numeric_value(value: object) -> float | None:
    """Keep absent or invalid telemetry unknown, including JSON booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) and value >= 0 else None


class Granary2Feeder(GranarySmartFeeder):
    """Reuse non-camera feeder entities and add single-bowl scale telemetry."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._settings_lock = Lock()

    async def refresh(self) -> None:
        """Refresh common data and independently fetch Granary 2 telemetry."""
        await super().refresh()
        for key, fetch in (
            ("dataRealInfo", self.api.device_data_real_info),
            ("freeFeedingSetting", self.api.device_get_free_feeding_setting),
        ):
            try:
                data = await fetch(self.serial)
            except (PetLibroAPIError, aiohttp.ClientError) as err:
                # Do not retain an old bowl weight or mode after a failed poll.
                data = {}
                _LOGGER.warning("Unable to refresh Granary 2 %s: %s", key, err)
            self.update_data({key: data if isinstance(data, dict) else {}})
        await self._refresh_consumption()

    async def _refresh_consumption(self) -> None:
        """Sum measured meal intake for today in the API's configured timezone."""
        now = utcnow()
        midnight = now.astimezone(ZoneInfo(self.api.time_zone)).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        data = {}
        try:
            records = await self.api.get_device_meal_records(
                self.serial, int(midnight.timestamp() * 1000), int(now.timestamp() * 1000),
            )
            total = 0.0
            for record in records:
                intake = _numeric_value(record.get("intake"))
                if intake is None:
                    raise PetLibroAPIError("Missing or invalid measured meal intake")
                # Top-level intake is already grams; petsInfo repeats that value.
                total += intake
            if not isfinite(total):
                raise PetLibroAPIError("Invalid measured consumption total")
            durations = [self._meal_duration(record) for record in records]
            data = {
                "weight": total, "reset": midnight, "count": len(records),
                "duration": sum(durations) if all(value is not None for value in durations) else None,
            }
            latest = records
            if not latest:
                try:
                    latest = await self.api.get_device_meal_records(
                        self.serial, int((now - timedelta(days=30)).timestamp() * 1000),
                        int(now.timestamp() * 1000), latest_only=True,
                    )
                except (PetLibroAPIError, aiohttp.ClientError):
                    latest = []
            timestamps = [_numeric_value(record.get("recordTime")) for record in latest]
            valid_times = [value for value in timestamps if value is not None]
            data["last_meal"] = datetime.fromtimestamp(max(valid_times) / 1000, ZoneInfo(self.api.time_zone)) if valid_times else None
        except (PetLibroAPIError, aiohttp.ClientError) as err:
            _LOGGER.warning("Unable to refresh Granary 2 consumption: %s", err)
        self.update_data({"consumptionToday": data})

    @staticmethod
    def _meal_duration(record: dict) -> int | None:
        """Use millisecond eating timestamps; top-level duration is rounded minutes."""
        start = _numeric_value(record.get("petEatingStartTime"))
        end = _numeric_value(record.get("eatingEndTime"))
        if start is None or end is None or end < start:
            return None
        return int((end - start) / 1000)

    @property
    def today_meal_count(self) -> int | None:
        return self._data.get("consumptionToday", {}).get("count")

    @property
    def today_meal_duration(self) -> float | None:
        return self._data.get("consumptionToday", {}).get("duration")

    @property
    def last_meal_time(self) -> datetime | None:
        return self._data.get("consumptionToday", {}).get("last_meal")

    @property
    def auto_feed_max_weight(self) -> float | None:
        return _numeric_value(self._data.get("dataRealInfo", {}).get("autoFeedMaxWeight"))

    @property
    def auto_stop_feed_enabled(self) -> bool | None:
        value = self._data.get("dataRealInfo", {}).get("autoStopFeedSwitch")
        return value if isinstance(value, bool) else None

    @property
    def child_lock_switch(self) -> bool | None:
        value = self._data.get("getAttributeSetting", {}).get("childLockSwitch")
        return value if isinstance(value, bool) else None

    @property
    def left_food_low(self) -> None:
        return None

    @property
    def right_food_low(self) -> None:
        return None

    def feeding_weight(self, portions: float) -> float | None:
        """Convert dispensed portions using the device's calibration, in grams."""
        ratio = self.portion_to_gram_ratio
        return round(portions * ratio, 2) if ratio is not None and ratio > 0 else None

    def feeding_amount(self, portions: float, unit: Unit | None) -> float | None:
        """Use calibration for mass; retain the existing volume conversion."""
        if unit in (Unit.GRAMS, Unit.OUNCES):
            grams = self.feeding_weight(portions)
            return round(MassConverter.convert(grams, UnitOfMass.GRAMS, unit.symbol), 2) if grams is not None else None
        return Unit.convert_feed(portions, None, unit, True)

    def portions_from_amount(self, value: float, unit: Unit | None) -> int:
        """Convert a manual quantity back to whole portions without dispensing."""
        if unit in (Unit.GRAMS, Unit.OUNCES):
            ratio = self.portion_to_gram_ratio
            if ratio is None or ratio <= 0:
                raise PetLibroAPIError("Grams per portion is unavailable")
            return round(MassConverter.convert(value, unit.symbol, UnitOfMass.GRAMS) / ratio)
        return Unit.convert_feed(value, unit, None)

    @property
    def meal_call_enabled(self) -> bool | None:
        value = self._data.get("dataRealInfo", {}).get("mealCallSwitch")
        return value if isinstance(value, bool) else None

    @property
    def meal_call_times(self) -> float | None:
        value = _numeric_value(self._data.get("dataRealInfo", {}).get("mealCallTimes"))
        return value if value is not None and value.is_integer() and 1 <= value <= 5 else None

    @property
    def meal_call_apply(self) -> list[str] | None:
        value = self._data.get("dataRealInfo", {}).get("mealCallApply")
        if not isinstance(value, list) or any(mode not in ("PLAN", "FREE", "MANUAL") for mode in value):
            return None
        return value

    @property
    def meal_call_settings_available(self) -> bool:
        return self.meal_call_enabled is not None and self.meal_call_times is not None and self.meal_call_apply is not None

    @property
    def meal_call_plan(self) -> bool | None:
        return "PLAN" in self.meal_call_apply if self.meal_call_apply is not None else None

    @property
    def meal_call_free(self) -> bool | None:
        return "FREE" in self.meal_call_apply if self.meal_call_apply is not None else None

    @property
    def meal_call_manual(self) -> bool | None:
        return "MANUAL" in self.meal_call_apply if self.meal_call_apply is not None else None

    async def set_free_feeding_setting(self, key: str, value: float) -> None:
        """Serialize edits and preserve the companion field from a fresh cloud read."""
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or int(value) != value:
            raise ValueError("Smart Refill values must be whole numbers")
        if key not in ("free_feeding_per_grain", "free_feeding_daily_max", "free_feeding_leftover_weight"):
            raise ValueError("Unsupported Smart Refill setting")
        async with self._settings_lock:
            if key == "free_feeding_leftover_weight":
                await self.api.update_free_feeding_threshold(self.serial, int(value))
            else:
                settings = await self.api.device_get_free_feeding_setting(self.serial)
                if not isinstance(settings, dict):
                    raise PetLibroAPIError("Smart Refill settings are unavailable; cannot preserve existing values")
                portions = settings.get("freePerGrainNum")
                daily_max = settings.get("freeDailyMaxNum")
                if key == "free_feeding_per_grain":
                    portions = int(value)
                else:
                    daily_max = int(value)
                await self.api.update_free_feeding_setting(self.serial, portions, daily_max)
            await self.refresh()

    async def set_meal_call_setting(self, key: str, value: bool | float) -> None:
        """Preserve switch, repetitions, and application modes on every edit."""
        if key not in ("meal_call_enabled", "meal_call_times", "meal_call_plan", "meal_call_free", "meal_call_manual"):
            raise ValueError("Unsupported Meal Call setting")
        async with self._settings_lock:
            settings = await self.api.device_data_real_info(self.serial)
            if not isinstance(settings, dict):
                raise PetLibroAPIError("Meal Call settings are unavailable; cannot preserve existing values")
            enabled = settings.get("mealCallSwitch")
            times = settings.get("mealCallTimes")
            apply = settings.get("mealCallApply")
            if not isinstance(enabled, bool) or not isinstance(times, int) or isinstance(times, bool) or not isinstance(apply, list):
                raise PetLibroAPIError("Meal Call settings are unavailable; cannot preserve existing values")
            apply = list(apply)
            if key == "meal_call_times":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or int(value) != value:
                    raise ValueError("Meal Call repetitions must be whole numbers")
                times = int(value)
            elif not isinstance(value, bool):
                raise ValueError("Meal Call switches require a boolean")
            elif key == "meal_call_enabled":
                enabled = value
            else:
                mode = {"meal_call_plan": "PLAN", "meal_call_free": "FREE", "meal_call_manual": "MANUAL"}[key]
                if value and mode not in apply:
                    apply.append(mode)
                elif not value:
                    apply = [item for item in apply if item != mode]
            await self.api.update_meal_call_setting(self.serial, enabled, times, apply)
            await self.refresh()

    @property
    def today_consumed_food_weight(self) -> float | None:
        """Return today's measured food intake in grams."""
        return self._data.get("consumptionToday", {}).get("weight")

    @property
    def consumption_last_reset(self) -> datetime | None:
        """Return the start of the day associated with a valid consumption total."""
        return self._data.get("consumptionToday", {}).get("reset")

    @property
    def available(self) -> bool:
        """Return the feeder's connection state."""
        return self.online

    @property
    def food_low(self) -> bool | None:
        """Use explicit food status; missing telemetry is not an empty tank."""
        info = self._data.get("realInfo", {})
        surplus = info.get("surplusGrain")
        if isinstance(surplus, bool):
            return not surplus
        if info.get("warehouseSurplusGrain") == "GOOD":
            return False
        return None

    @property
    def remaining_food_weight(self) -> float | None:
        """Return the single bowl's measured food weight in grams."""
        return _numeric_value(self._data.get("dataRealInfo", {}).get("remainingGrain"))

    @property
    def max_feedable(self) -> float | None:
        """Return additional portions that fit in the bowl."""
        return _numeric_value(self._data.get("dataRealInfo", {}).get("maxFeedable"))

    @property
    def portion_to_gram_ratio(self) -> float | None:
        """Return the device's calibrated grams per portion."""
        return _numeric_value(self._data.get("dataRealInfo", {}).get("portionToGramRatio"))

    @property
    def feeding_mode(self) -> str | None:
        """Return the feeding mode as a Home Assistant enum state."""
        value = self._data.get("dataRealInfo", {}).get("feedingMode")
        return value.lower() if value in ("FREE", "PLAN") else None

    @property
    def free_feeding_per_grain(self) -> float | None:
        """Return portions dispensed per Smart Refill."""
        return _numeric_value(self._data.get("freeFeedingSetting", {}).get("freePerGrainNum"))

    @property
    def free_feeding_daily_max(self) -> float | None:
        """Return the Smart Refill daily limit in portions."""
        return _numeric_value(self._data.get("freeFeedingSetting", {}).get("freeDailyMaxNum"))

    @property
    def free_feeding_leftover_weight(self) -> float | None:
        """Return the Smart Refill threshold in grams."""
        return _numeric_value(self._data.get("freeFeedingSetting", {}).get("freeLeftoverWeight"))

    @property
    def free_feeding_wait_seconds(self) -> float | None:
        """Return the Smart Refill wait interval in seconds."""
        return _numeric_value(self._data.get("freeFeedingSetting", {}).get("freeWaitSeconds"))
