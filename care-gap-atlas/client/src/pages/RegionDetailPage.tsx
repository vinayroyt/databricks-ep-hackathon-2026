import { useEffect, useState, useRef } from 'react';
import { useParams, useNavigate } from 'react-router';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Skeleton,
  Textarea,
} from '@databricks/appkit-ui/react';
import { ChevronLeft, ChevronDown, ChevronRight, Trash2, AlertCircle } from 'lucide-react';
import type { Region, Facility, Annotation } from '../types';
import { gapScoreColor, gapScoreLabel, gapScoreBg, trustBg, trustColor } from '../types';

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.75 ? 'bg-green-500' : value >= 0.5 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="h-1.5 w-16 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-muted-foreground">{pct}%</span>
    </div>
  );
}

function FacilityRow({ facility }: { facility: Facility }) {
  const [open, setOpen] = useState(false);
  const evidenceEntries = Object.entries(facility.extracted.evidence);

  return (
    <div className="border rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-start gap-3 p-3 hover:bg-muted/50 transition-colors text-left"
      >
        <span className="mt-0.5 text-muted-foreground flex-shrink-0">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="font-medium text-sm">{facility.name}</span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${trustBg(facility.trust_score)}`}>
              Trust {Math.round(facility.trust_score * 100)}%
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {facility.trust_flags.map((flag) => (
              <Badge key={flag} variant="destructive" className="text-xs">
                {flag}
              </Badge>
            ))}
            {facility.extracted.specialties.map((s) => (
              <Badge key={s} variant="secondary" className="text-xs">
                {s}
              </Badge>
            ))}
          </div>
        </div>
      </button>

      {open && (
        <div className="border-t bg-muted/30 p-3 space-y-3 text-sm">
          <div>
            <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
              Source text
            </div>
            <p className="text-xs italic text-muted-foreground bg-background/60 rounded p-2">
              &ldquo;{facility.raw_text}&rdquo;
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                Extracted fields
              </div>
              <div className="space-y-1 text-xs">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Total beds</span>
                  <span>{facility.extracted.bed_count ?? '—'}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">ICU beds</span>
                  <span className={facility.extracted.icu_beds === 0 && facility.extracted.specialties.includes('ICU') ? 'text-red-600 font-semibold' : ''}>
                    {facility.extracted.icu_beds}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Equipment</span>
                  <span>{facility.extracted.equipment.length ? facility.extracted.equipment.join(', ') : '—'}</span>
                </div>
              </div>
            </div>

            <div>
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                Field confidence
              </div>
              <div className="space-y-1">
                {Object.entries(facility.extracted.confidence).map(([field, val]) => (
                  <div key={field} className="flex items-center justify-between gap-2">
                    <span className="text-xs text-muted-foreground capitalize">{field.replace('_', ' ')}</span>
                    <ConfidenceBar value={val ?? 0} />
                  </div>
                ))}
              </div>
            </div>
          </div>

          {evidenceEntries.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                Evidence snippets
              </div>
              <div className="space-y-1">
                {evidenceEntries.map(([field, text]) => text && (
                  <div key={field} className="text-xs bg-background/60 rounded p-2">
                    <span className="font-medium capitalize">{field.replace('_', ' ')}: </span>
                    <span className="text-muted-foreground">{text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AnnotationPanel({ regionId }: { regionId: string }) {
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');
  const [author, setAuthor] = useState('');
  const noteRef = useRef<HTMLTextAreaElement>(null);

  const loadAnnotations = () => {
    fetch(`/api/annotations?region_id=${encodeURIComponent(regionId)}`)
      .then((r) => r.json() as Promise<Annotation[]>)
      .then(setAnnotations)
      .catch(console.error)
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadAnnotations();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regionId]);

  const handleSave = async () => {
    if (!note.trim()) return;
    setSaving(true);
    try {
      const resp = await fetch('/api/annotations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ region_id: regionId, note: note.trim(), author: author.trim() || undefined }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      setNote('');
      loadAnnotations();
    } catch (err) {
      console.error('Save failed:', err);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await fetch(`/api/annotations/${id}`, { method: 'DELETE' });
      setAnnotations((prev) => prev.filter((a) => a.id !== id));
    } catch (err) {
      console.error('Delete failed:', err);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Planner Notes</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : annotations.length === 0 ? (
          <p className="text-xs text-muted-foreground text-center py-4">No notes yet for this region.</p>
        ) : (
          <div className="space-y-2 max-h-64 overflow-y-auto">
            {annotations.map((ann) => (
              <div key={ann.id} className="bg-muted/40 rounded-lg p-3 text-sm group relative">
                <p className="pr-6">{ann.note}</p>
                <div className="text-xs text-muted-foreground mt-1 flex items-center gap-1.5">
                  {ann.author && <span className="font-medium">{ann.author}</span>}
                  {ann.author && <span>·</span>}
                  <span>{new Date(ann.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                  {ann.facility_id && (
                    <>
                      <span>·</span>
                      <span className="italic">facility {ann.facility_id}</span>
                    </>
                  )}
                </div>
                <button
                  onClick={() => void handleDelete(ann.id)}
                  className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                  aria-label="Delete note"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-2 border-t pt-3">
          <Textarea
            ref={noteRef}
            placeholder="Add a planner note for this region…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            className="text-sm resize-none"
          />
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Your name (optional)"
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              className="flex-1 rounded-md border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            />
            <Button
              size="sm"
              onClick={() => void handleSave()}
              disabled={saving || !note.trim()}
            >
              {saving ? 'Saving…' : 'Save Note'}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function RegionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [region, setRegion] = useState<Region | null>(null);
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    Promise.all([
      fetch(`/api/regions/${id}`).then((r) => {
        if (!r.ok) throw new Error(`Region not found`);
        return r.json() as Promise<Region>;
      }),
      fetch(`/api/regions/${id}/facilities`).then((r) => r.json() as Promise<Facility[]>),
    ])
      .then(([reg, facs]) => {
        setRegion(reg);
        setFacilities(facs);
      })
      .catch((e: unknown) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto space-y-4">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-32 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  if (error || !region) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center gap-2 text-destructive p-4 bg-destructive/10 rounded-lg">
          <AlertCircle className="h-5 w-5" />
          <span className="text-sm">{error ?? 'Region not found'}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <button
        onClick={() => void navigate('/')}
        className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronLeft className="h-4 w-4" />
        All regions
      </button>

      <Card className={`border ${gapScoreBg(region.gap_score)}`}>
        <CardContent className="pt-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="text-2xl font-bold">{region.region_name}</h2>
              <p className="text-sm text-muted-foreground mt-0.5">{region.summary}</p>
            </div>
            <div className="text-right">
              <div className={`text-4xl font-bold ${gapScoreColor(region.gap_score)}`}>
                {Math.round(region.gap_score * 100)}%
              </div>
              <div className="text-xs text-muted-foreground">{gapScoreLabel(region.gap_score)} gap</div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 mt-4 max-w-xs">
            <div className="bg-background/60 rounded p-2 text-center">
              <div className="font-bold">{region.claimed_icu_facilities}</div>
              <div className="text-xs text-muted-foreground">Claim ICU</div>
            </div>
            <div className="bg-background/60 rounded p-2 text-center">
              <div className={`font-bold ${region.verified_icu_facilities === 0 ? 'text-destructive' : 'text-green-600'}`}>
                {region.verified_icu_facilities}
              </div>
              <div className="text-xs text-muted-foreground">Verified ICU</div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-3">
          <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide">
            Facilities ({facilities.length})
          </h3>
          {facilities.length === 0 ? (
            <p className="text-sm text-muted-foreground">No facilities found for this region.</p>
          ) : (
            <div className="space-y-2">
              {facilities.map((f) => (
                <FacilityRow key={f.facility_id} facility={f} />
              ))}
            </div>
          )}
          <p className="text-xs text-muted-foreground">
            <span className={`font-medium ${trustColor(0.3)}`}>Red</span> trust = low confidence claims.{' '}
            Expand a row to see evidence snippets and field confidence scores.
          </p>
        </div>

        <div>
          <h3 className="font-semibold text-sm text-muted-foreground uppercase tracking-wide mb-3">
            Saved Notes
          </h3>
          {id && <AnnotationPanel regionId={id} />}
        </div>
      </div>
    </div>
  );
}
