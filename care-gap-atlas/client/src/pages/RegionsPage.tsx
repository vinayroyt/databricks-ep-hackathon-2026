import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { Card, CardContent, CardHeader, CardTitle, Skeleton } from '@databricks/appkit-ui/react';
import { ArrowRight, AlertCircle } from 'lucide-react';
import type { Region } from '../types';
import { gapScoreLabel, gapScoreColor, gapScoreBg, gapScoreBar } from '../types';
import { RegionMap } from '../components/RegionMap';

function MapSkeleton() {
  return <Skeleton className="w-full rounded-xl" style={{ height: '420px' }} />;
}

export function RegionsPage() {
  const [regions, setRegions] = useState<Region[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    fetch('/api/regions')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<Region[]>;
      })
      .then(setRegions)
      .catch((e: unknown) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto space-y-6">
        <div>
          <Skeleton className="h-8 w-64 mb-2" />
          <Skeleton className="h-4 w-96" />
        </div>
        <MapSkeleton />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2].map((i) => <Skeleton key={i} className="h-52 rounded-xl" />)}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center gap-2 text-destructive p-4 bg-destructive/10 rounded-lg">
          <AlertCircle className="h-5 w-5 flex-shrink-0" />
          <span className="text-sm">Failed to load regions: {error}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-foreground">Care Gap Map</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Click a region to see which facilities are missing capabilities and why.
          </p>
        </div>
        <div className="flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded-full bg-red-500" /> Critical ≥ 60%
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded-full bg-amber-500" /> Moderate 30–60%
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-3 rounded-full bg-green-500" /> Low &lt; 30%
          </span>
        </div>
      </div>

      {regions.length > 0 && <RegionMap regions={regions} />}

      <div>
        <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide mb-3">
          All regions
        </h3>
        {regions.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">No regions found.</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {regions.map((region) => (
              <button
                key={region.region_id}
                onClick={() => void navigate(`/region/${region.region_id}`)}
                className="text-left group"
              >
                <Card className={`border transition-shadow hover:shadow-md ${gapScoreBg(region.gap_score)}`}>
                  <CardHeader className="pb-2">
                    <div className="flex items-start justify-between">
                      <CardTitle className="text-lg">{region.region_name}</CardTitle>
                      <span
                        className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${gapScoreBg(region.gap_score)} ${gapScoreColor(region.gap_score)}`}
                      >
                        {gapScoreLabel(region.gap_score)}
                      </span>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div>
                      <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
                        <span>Care gap score</span>
                        <span className={`font-bold text-base ${gapScoreColor(region.gap_score)}`}>
                          {Math.round(region.gap_score * 100)}%
                        </span>
                      </div>
                      <div className="h-2 bg-muted rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${gapScoreBar(region.gap_score)}`}
                          style={{ width: `${region.gap_score * 100}%` }}
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-sm">
                      <div className="bg-background/60 rounded p-2 text-center">
                        <div className="font-bold text-lg">{region.claimed_icu_facilities}</div>
                        <div className="text-xs text-muted-foreground">Claim ICU</div>
                      </div>
                      <div className="bg-background/60 rounded p-2 text-center">
                        <div className={`font-bold text-lg ${region.verified_icu_facilities === 0 ? 'text-destructive' : 'text-green-600'}`}>
                          {region.verified_icu_facilities}
                        </div>
                        <div className="text-xs text-muted-foreground">Verified ICU</div>
                      </div>
                    </div>

                    <p className="text-xs text-muted-foreground leading-relaxed">{region.summary}</p>

                    <div className="flex items-center gap-1 text-xs font-medium text-primary group-hover:gap-2 transition-all">
                      View facilities &amp; notes <ArrowRight className="h-3 w-3" />
                    </div>
                  </CardContent>
                </Card>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
