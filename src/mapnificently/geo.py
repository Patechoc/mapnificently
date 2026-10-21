"""A local metric projection, accurate enough at city scale."""

import math

import numpy as np
import numpy.typing as npt

EARTH_RADIUS_M = 6_371_008.8

FloatArray = npt.NDArray[np.float64]


class LocalProjection:
    """Equirectangular projection centred on a city: metres east/north of the centre."""

    def __init__(self, lat0: float, lon0: float) -> None:
        self.lat0 = lat0
        self.lon0 = lon0
        self._kx = EARTH_RADIUS_M * math.cos(math.radians(lat0)) * math.pi / 180
        self._ky = EARTH_RADIUS_M * math.pi / 180

    def forward(
        self, lat: FloatArray | float, lon: FloatArray | float
    ) -> tuple[FloatArray, FloatArray]:
        """Latitude/longitude to (x, y) metres."""
        x = (np.asarray(lon, dtype=np.float64) - self.lon0) * self._kx
        y = (np.asarray(lat, dtype=np.float64) - self.lat0) * self._ky
        return x, y

    def inverse(self, x: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
        """(x, y) metres to latitude/longitude."""
        lat = np.asarray(y, dtype=np.float64) / self._ky + self.lat0
        lon = np.asarray(x, dtype=np.float64) / self._kx + self.lon0
        return lat, lon
