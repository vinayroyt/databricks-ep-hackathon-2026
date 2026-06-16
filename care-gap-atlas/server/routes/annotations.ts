import { z } from 'zod';
import { Application } from 'express';

interface AppKitLakebase {
  lakebase: {
    query(text: string, params?: unknown[]): Promise<{ rows: Record<string, unknown>[] }>;
  };
  server: {
    extend(fn: (app: Application) => void): void;
  };
}

const SETUP_SQL = `
  CREATE TABLE IF NOT EXISTS region_annotations (
    id SERIAL PRIMARY KEY,
    region_id TEXT NOT NULL,
    facility_id TEXT,
    author TEXT,
    note TEXT NOT NULL,
    is_test BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  )
`;

const CreateBody = z.object({
  region_id: z.string().min(1),
  facility_id: z.string().optional(),
  author: z.string().optional(),
  note: z.string().min(1),
});

export async function setupAnnotationRoutes(appkit: AppKitLakebase): Promise<void> {
  try {
    await appkit.lakebase.query(SETUP_SQL);
    console.log('[annotations] Table region_annotations ready');
  } catch (err) {
    console.warn('[annotations] Table setup warning:', (err as Error).message);
    console.warn('[annotations] If the table already exists with a different owner, GRANT INSERT,SELECT,DELETE ON region_annotations TO current_user;');
  }

  appkit.server.extend((app) => {
    app.get('/api/annotations', async (req, res) => {
      const conditions: string[] = ['is_test = false'];
      const params: unknown[] = [];

      const { region_id, facility_id } = req.query;
      if (region_id && typeof region_id === 'string') {
        params.push(region_id);
        conditions.push(`region_id = $${params.length}`);
      }
      if (facility_id && typeof facility_id === 'string') {
        params.push(facility_id);
        conditions.push(`facility_id = $${params.length}`);
      }

      const where = `WHERE ${conditions.join(' AND ')}`;
      try {
        const result = await appkit.lakebase.query(
          `SELECT id, region_id, facility_id, author, note, created_at
           FROM region_annotations ${where}
           ORDER BY created_at DESC`,
          params,
        );
        res.json(result.rows);
      } catch (err) {
        console.error('Failed to list annotations:', err);
        res.status(500).json({ error: 'Failed to fetch annotations' });
      }
    });

    app.post('/api/annotations', async (req, res) => {
      const parsed = CreateBody.safeParse(req.body);
      if (!parsed.success) {
        res.status(400).json({ error: 'region_id and note are required' });
        return;
      }
      const { region_id, facility_id, author, note } = parsed.data;
      try {
        const result = await appkit.lakebase.query(
          `INSERT INTO region_annotations (region_id, facility_id, author, note)
           VALUES ($1, $2, $3, $4)
           RETURNING id, region_id, facility_id, author, note, created_at`,
          [region_id, facility_id ?? null, author ?? null, note],
        );
        res.status(201).json(result.rows[0]);
      } catch (err) {
        console.error('Failed to create annotation:', err);
        res.status(500).json({ error: 'Failed to save annotation' });
      }
    });

    app.delete('/api/annotations/:id', async (req, res) => {
      const id = parseInt(req.params.id, 10);
      if (isNaN(id)) {
        res.status(400).json({ error: 'Invalid id' });
        return;
      }
      try {
        const result = await appkit.lakebase.query(
          'DELETE FROM region_annotations WHERE id = $1 RETURNING id',
          [id],
        );
        if (result.rows.length === 0) {
          res.status(404).json({ error: 'Annotation not found' });
          return;
        }
        res.status(204).send();
      } catch (err) {
        console.error('Failed to delete annotation:', err);
        res.status(500).json({ error: 'Failed to delete annotation' });
      }
    });
  });
}
