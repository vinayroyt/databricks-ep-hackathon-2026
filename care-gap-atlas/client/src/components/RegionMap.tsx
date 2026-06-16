import { useNavigate } from 'react-router';
import { MapContainer, TileLayer, CircleMarker, Tooltip, Popup } from 'react-leaflet';
import type { Region } from '../types';
import { gapScoreLabel } from '../types';

const REGION_COORDS: Record<string, [number, number]> = {
  R01: [17.914, 77.518], // Bidar district
  R02: [17.542, 77.243], // Aurad taluk
};

const MAP_CENTER: [number, number] = [17.73, 77.38];
const MAP_ZOOM = 9;

function gapToColor(score: number): string {
  if (score >= 0.6) return '#DC2626'; // red-600
  if (score >= 0.3) return '#D97706'; // amber-600
  return '#16A34A'; // green-600
}

interface Props {
  regions: Region[];
}

export function RegionMap({ regions }: Props) {
  const navigate = useNavigate();

  return (
    <div className="rounded-xl overflow-hidden border shadow-sm" style={{ height: '420px' }}>
      <MapContainer
        center={MAP_CENTER}
        zoom={MAP_ZOOM}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom={false}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
          subdomains="abcd"
          maxZoom={19}
        />

        {regions.map((region) => {
          const coords = REGION_COORDS[region.region_id];
          if (!coords) return null;
          const color = gapToColor(region.gap_score);
          const pct = Math.round(region.gap_score * 100);

          return (
            <CircleMarker
              key={region.region_id}
              center={coords}
              radius={22}
              pathOptions={{
                fillColor: color,
                fillOpacity: 0.85,
                color: '#fff',
                weight: 2.5,
              }}
            >
              <Tooltip direction="top" offset={[0, -20]} permanent>
                <span className="font-semibold text-xs">{region.region_name}</span>
              </Tooltip>

              <Popup minWidth={220} maxWidth={280}>
                <div className="space-y-2 py-1">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-bold text-base text-gray-900">{region.region_name}</span>
                    <span
                      className="text-xs font-semibold px-1.5 py-0.5 rounded"
                      style={{ background: color + '22', color }}
                    >
                      {gapScoreLabel(region.gap_score)}
                    </span>
                  </div>

                  <div>
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>Care gap score</span>
                      <span className="font-bold" style={{ color }}>{pct}%</span>
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{ width: `${pct}%`, background: color }}
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-1.5 text-xs">
                    <div className="bg-gray-50 rounded p-1.5 text-center">
                      <div className="font-bold text-sm">{region.claimed_icu_facilities}</div>
                      <div className="text-gray-500">Claim ICU</div>
                    </div>
                    <div className="bg-gray-50 rounded p-1.5 text-center">
                      <div className="font-bold text-sm" style={{ color: region.verified_icu_facilities === 0 ? '#DC2626' : '#16A34A' }}>
                        {region.verified_icu_facilities}
                      </div>
                      <div className="text-gray-500">Verified ICU</div>
                    </div>
                  </div>

                  <p className="text-xs text-gray-600 leading-snug">{region.summary}</p>

                  <button
                    onClick={() => void navigate(`/region/${region.region_id}`)}
                    className="w-full text-xs font-semibold text-white rounded py-1.5 px-3 mt-1 transition-opacity hover:opacity-90"
                    style={{ background: '#FF3621' }}
                  >
                    View facilities &amp; notes →
                  </button>
                </div>
              </Popup>
            </CircleMarker>
          );
        })}
      </MapContainer>
    </div>
  );
}
