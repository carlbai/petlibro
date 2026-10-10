"""Camera-free Granary 2 (PLAF105) support."""

from logging import getLogger
from math import isfinite

import aiohttp

from ...exceptions import PetLibroAPIError
from .granary_smart_feeder import GranarySmartFeeder

_LOGGER = getLogger(__name__)


def _numeric_value(value: object) -> float | None:
    """Keep absent or invalid telemetry unknown, including JSON booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) and value >= 0 else None


class Granary2Feeder(GranarySmartFeeder):
    """Reuse non-camera feeder entities and add single-bowl scale telemetry."""

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
        """Return Smart Refill (FREE) or scheduled feeding (PLAN)."""
        value = self._data.get("dataRealInfo", {}).get("feedingMode")
        return value if value in ("FREE", "PLAN") else None

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
