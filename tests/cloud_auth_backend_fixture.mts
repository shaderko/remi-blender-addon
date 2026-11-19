/** Loopback-only bridge to the real Cloud auth implementation, with an ephemeral DB.
 * Launched by test_cloud_backend.py; never imports production config or providers.
 */
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const backend = resolve(process.argv[2], "apps/backend");
const require = createRequire(resolve(backend, "package.json"));
const load = (name: string) => import(pathToFileURL(require.resolve(name)).href);
const { betterAuth } = await load("better-auth");
const { memoryAdapter } = await load("better-auth/adapters/memory");
const { createCliAuthPlugins } = await import(pathToFileURL(resolve(backend, "lib/cli-auth.ts")).href);
const { createCloudApiRoutes } = await import(pathToFileURL(resolve(backend, "features/cloud-api/routes.ts")).href);

let auth: any;
const api = createCloudApiRoutes(); // Same default disabled admission as src/index.ts.
const server = createServer(async (incoming, outgoing) => {
  try {
    const chunks = [];
    for await (const chunk of incoming) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    const request = new Request(`${base}${incoming.url}`, {
      method: incoming.method,
      headers: incoming.headers as Record<string, string>,
      ...(body.length ? { body } : {}),
    });
    const response = incoming.url?.startsWith("/api/auth/")
      ? await auth.handler(request)
      : await api.request(new Request(`${base}${incoming.url?.replace(/^\/api\/v1/, "")}`, request));
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch {
    outgoing.writeHead(500); outgoing.end("fixture bridge failed");
  }
});
await new Promise<void>((ready) => server.listen(0, "127.0.0.1", ready));
const address = server.address() as { port: number };
const base = `http://127.0.0.1:${address.port}`;
auth = betterAuth({
  baseURL: base,
  secret: "test-only-remi-blender-interop-secret-not-production",
  database: memoryAdapter({ user: [], account: [], session: [], verification: [], deviceCode: [] }),
  emailAndPassword: { enabled: true },
  plugins: createCliAuthPlugins(`${base}/auth/device`),
  rateLimit: { enabled: false },
});
console.log(`REMI_AUTH_FIXTURE ${base}`);
