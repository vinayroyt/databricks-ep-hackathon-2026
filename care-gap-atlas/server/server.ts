import { createApp, lakebase, server, serving } from '@databricks/appkit';
import { setupAnnotationRoutes } from './routes/annotations';
import { setupRegionRoutes } from './routes/regions';

createApp({
  plugins: [
    lakebase(),
    server(),
    serving(),
  ],
  async onPluginsReady(appkit) {
    appkit.server.extend((app) => {
      setupRegionRoutes(app);
    });
    await setupAnnotationRoutes(appkit);
  },
}).catch(console.error);
