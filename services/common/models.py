"""Domain records shared by the ingest, forecast and API services."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

HORIZONS_MIN: tuple[int, ...] = (15, 30, 60)
BUCKET_MIN: int = 15
"""Width of the continuous-aggregate bucket, in minutes."""


class Station(BaseModel):
    station_id: str
    name: str
    lat: float
    lon: float
    address: str = ""
    capacity: int = 0


class Observation(BaseModel):
    """One station reading at one point in time."""

    station_id: str
    ts: datetime
    num_bikes: int
    num_docks: int
    bikes_disabled: int = 0
    docks_disabled: int = 0
    is_renting: bool = True
    is_returning: bool = True

    def key(self) -> str:
        return self.station_id


class WeatherPoint(BaseModel):
    ts: datetime
    temp_c: float
    precip_mm: float
    wind_kmh: float


class StationSnapshot(BaseModel):
    """A station joined with its most recent observation (the /stations payload)."""

    station_id: str
    name: str
    lat: float
    lon: float
    capacity: int
    num_bikes: int
    num_docks: int
    ts: datetime
    occupancy: float = Field(
        description="num_bikes / capacity, clipped to [0, 1]; 0 when capacity is unknown"
    )


class HistoryPoint(BaseModel):
    """One 15-minute continuous-aggregate bucket."""

    bucket: datetime
    avg_bikes: float
    min_bikes: int
    max_bikes: int
    avg_docks: float
    samples: int


class Prediction(BaseModel):
    horizon_min: int
    predicted_bikes: float
    target_ts: datetime


class PredictResponse(BaseModel):
    station_id: str
    as_of: datetime
    current_bikes: int
    capacity: int
    model: str
    predictions: list[Prediction]


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
