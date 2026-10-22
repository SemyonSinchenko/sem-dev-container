// searxng-search — DSH web_search provider backed by the host's SearXNG
// instance. Loaded by dsh-web-entry for every run-dsh container.
//
// The native `web_search` tool calls ctx.web.search(); this provider maps the
// normalized request to SearXNG's JSON API and returns the portable
// `{ url, title, snippet, publishedAt }` source shape. No API key is needed.
//
// Configuration (container environment, set by bins/run-dsh):
//   SEARXNG_URL          default http://127.0.0.1:7331
//   SEARXNG_TIMEOUT_MS   default 30000
//
// The Podman launchers publish the host's 127.0.0.1:7331 into the container's
// loopback (pasta -T,7331), so the default works for run-dsh/run-pi/run-pa.
// Under Docker, publish SearXNG on a host-reachable address and pass
// `--env SEARXNG_URL=http://<host>:7331`.

const DEFAULT_URL = "http://127.0.0.1:7331";
const DEFAULT_TIMEOUT_MS = 30_000;
const USER_AGENT = "deepseek-harness/searxng";

/** Parse a positive integer timeout, falling back to the default. */
function timeoutFromEnv() {
  const raw = process.env.SEARXNG_TIMEOUT_MS;
  if (raw === undefined || raw.trim() === "") return DEFAULT_TIMEOUT_MS;
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : DEFAULT_TIMEOUT_MS;
}

/** True when the configured value is a usable HTTP(S) base URL. */
function usableUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

/** Deduplicate and project SearXNG results into the WebSearchSource shape. */
function projectResults(payload) {
  if (payload === null || typeof payload !== "object" || !Array.isArray(payload.results)) {
    throw new Error("SearXNG returned an unexpected JSON shape (missing results[])");
  }

  const seen = new Set();
  const sources = [];
  for (const item of payload.results) {
    if (item === null || typeof item !== "object") continue;
    const url = item.url;
    if (typeof url !== "string" || url.length === 0 || seen.has(url)) continue;
    seen.add(url);
    sources.push({
      url,
      ...typeof item.title === "string" && item.title.length > 0
        ? { title: item.title }
        : {},
      ...typeof item.content === "string" && item.content.length > 0
        ? { snippet: item.content }
        : {},
      ...typeof item.publishedDate === "string" && item.publishedDate.length > 0
        ? { publishedAt: item.publishedDate }
        : {},
    });
  }
  return { sources, truncated: false };
}

/** The SearXNG-backed implementation of the WebSearchProvider interface. */
export class SearxngSearchProvider {
  id = "searxng";
  url;
  timeoutMs;

  constructor({ url, timeoutMs }) {
    this.url = url;
    this.timeoutMs = timeoutMs;
  }

  /** Cheap local check: a parseable HTTP(S) base URL. Never calls the network. */
  available() {
    return usableUrl(this.url);
  }

  /**
   * Run one search through SearXNG's JSON API.
   * @param {{ query: string, maxResults?: number }} request
   * @param {AbortSignal | undefined} signal
   * @returns {Promise<{ sources: object[], truncated: boolean }>}
   */
  async search(request, signal) {
    const base = this.url.endsWith("/") ? this.url : `${this.url}/`;
    const endpoint = new URL("search", base);
    endpoint.searchParams.set("q", request.query);
    endpoint.searchParams.set("format", "json");
    // General web results; SearXNG's default categories can include images.
    endpoint.searchParams.set("categories", "general");

    const timeout = AbortSignal.timeout(this.timeoutMs);
    const combined = signal === undefined ? timeout : AbortSignal.any([signal, timeout]);

    let response;
    try {
      response = await fetch(endpoint, {
        method: "GET",
        redirect: "error",
        headers: {
          accept: "application/json",
          "user-agent": USER_AGENT,
        },
        signal: combined,
      });
    } catch (error) {
      if (signal?.aborted === true) throw new Error("SearXNG search aborted");
      throw new Error(
        `SearXNG request failed at ${endpoint.origin}: ${String(error)}. ` +
          "Check that the host SearXNG is running and SEARXNG_URL is reachable " +
          "from this container.",
        { cause: error },
      );
    }

    if (!response.ok) {
      throw new Error(
        `SearXNG returned HTTP ${response.status} for ${endpoint.pathname}. ` +
          "Check the instance status and that its JSON format is enabled.",
      );
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new Error(
        "SearXNG returned a non-JSON response. Enable `json` in the instance's " +
          "search.formats setting (or use a JSON-capable instance).",
        { cause: error },
      );
    }

    return projectResults(payload);
  }
}

export const name = "searxng-search";
export const inject = ["web"];

/** Register the provider with the DSH web seam. */
export function apply(ctx) {
  const url = process.env.SEARXNG_URL?.trim() || DEFAULT_URL;
  const timeoutMs = timeoutFromEnv();
  if (!usableUrl(url)) {
    ctx.logger.warn(`searxng-search: invalid SEARXNG_URL ${JSON.stringify(url)}; provider unavailable`);
  } else {
    ctx.logger.info(`searxng-search: using ${url}`);
  }
  ctx.web.registerSearchProvider(new SearxngSearchProvider({ url, timeoutMs }));
}

export default { name, inject, apply };
