import maplibregl, { type GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useRef } from "react";

import type { StationSnapshot } from "../types";

const LJUBLJANA: [number, number] = [14.5058, 46.0511];
const STYLE_URL = "/osm-style.json";
const SOURCE_ID = "stations";

function toGeoJSON(stations: StationSnapshot[]): GeoJSON.FeatureCollection<GeoJSON.Point> {
  return {
    type: "FeatureCollection",
    features: stations.map((s) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      properties: {
        station_id: s.station_id,
        name: s.name,
        bikes: s.num_bikes,
        docks: s.num_docks,
        capacity: s.capacity,
        occupancy: s.occupancy,
      },
    })),
  };
}

interface Props {
  stations: StationSnapshot[];
  selectedId: string | null;
  onSelect: (stationId: string) => void;
}

/**
 * MapLibre GL map over keyless OSM raster tiles. Two data-driven layers share one
 * GeoJSON source: a heatmap weighted by bikes available (the city-wide read) and
 * a circle layer coloured by occupancy (the per-station read and click target).
 */
export function MapView({ stations, selectedId, onSelect }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const readyRef = useRef(false);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL,
      center: LJUBLJANA,
      zoom: 13,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    // "style.load" (not "load") so the station layers appear even when the
    // external raster tiles are slow or blocked — the data is what matters.
    map.on("style.load", () => {
      // promoteId lets setFeatureState address features by station_id.
      map.addSource(SOURCE_ID, {
        type: "geojson",
        data: toGeoJSON([]),
        promoteId: "station_id",
      });

      map.addLayer({
        id: "stations-heat",
        type: "heatmap",
        source: SOURCE_ID,
        maxzoom: 16,
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["get", "bikes"], 0, 0, 25, 1],
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 11, 1, 16, 3],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 11, 18, 16, 46],
          "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0.75, 16, 0.15],
          "heatmap-color": [
            "interpolate",
            ["linear"],
            ["heatmap-density"],
            0,
            "rgba(14,17,22,0)",
            0.2,
            "#1d4e89",
            0.45,
            "#2e9e9b",
            0.7,
            "#f2c14e",
            1,
            "#f25f5c",
          ],
        },
      });

      map.addLayer({
        id: "stations-circle",
        type: "circle",
        source: SOURCE_ID,
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            ["interpolate", ["linear"], ["get", "capacity"], 5, 3, 40, 7],
            16,
            ["interpolate", ["linear"], ["get", "capacity"], 5, 7, 40, 18],
          ],
          "circle-color": [
            "interpolate",
            ["linear"],
            ["get", "occupancy"],
            0,
            "#f25f5c",
            0.25,
            "#f2c14e",
            0.6,
            "#2e9e9b",
            1,
            "#1d4e89",
          ],
          "circle-stroke-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1],
          "circle-stroke-color": [
            "case",
            ["boolean", ["feature-state", "selected"], false],
            "#ffffff",
            "rgba(255,255,255,0.45)",
          ],
          "circle-opacity": 0.92,
        },
      });

      const popup = new maplibregl.Popup({ closeButton: false, offset: 10 });
      map.on("mouseenter", "stations-circle", (event) => {
        map.getCanvas().style.cursor = "pointer";
        const feature = event.features?.[0];
        if (!feature) return;
        const p = feature.properties as Record<string, string | number>;
        popup
          .setLngLat(event.lngLat)
          .setText(`${String(p.name)} — ${String(p.bikes)}/${String(p.capacity)} bikes`)
          .addTo(map);
      });
      map.on("mouseleave", "stations-circle", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });
      map.on("click", "stations-circle", (event) => {
        const id = event.features?.[0]?.properties?.station_id;
        if (typeof id === "string") selectRef.current(id);
      });

      readyRef.current = true;
    });

    return () => {
      readyRef.current = false;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Push new station data into the existing source rather than re-creating layers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const push = () => {
      const source = map.getSource(SOURCE_ID) as GeoJSONSource | undefined;
      if (!source) return;
      source.setData(toGeoJSON(stations));
      for (const s of stations) {
        map.setFeatureState(
          { source: SOURCE_ID, id: s.station_id },
          { selected: s.station_id === selectedId },
        );
      }
    };
    if (readyRef.current) push();
    else map.once("style.load", push);
  }, [stations, selectedId]);

  return <div className="map" ref={containerRef} />;
}
