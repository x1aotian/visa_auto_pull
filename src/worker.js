const SUPABASE_URL = "https://sdetncywjtheyqwfzshc.supabase.co";
const SUPABASE_ANON_KEY =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNkZXRuY3l3anRoZXlxd2Z6c2hjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NTQxMTMsImV4cCI6MjA4OTIzMDExM30." +
  "QGZoQrxIZ9pKbMSou5QEklxVfmV_p1G9imJW3soQwqM";

const CITY_ALIASES = {
  cnBEI: ["beijing", "北京"],
  cnSHA: ["shanghai", "上海"],
  cnGUA: ["guangzhou", "广州"],
  cnSHE: ["shenyang", "沈阳"],
  cnWUH: ["wuhan", "武汉"],
  cnCHE: ["chengdu", "成都"],
  hkHON: ["hong kong", "香港"],
  krSEO: ["seoul", "首尔"],
  jpTKY: ["tokyo", "东京"],
  sgSGP: ["singapore", "新加坡"],
};

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runMonitor(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/run") {
      return new Response("Visa slot monitor is alive. Visit /run to execute once.", { status: 200 });
    }

    const result = await runMonitor(env, { dryRun: url.searchParams.get("dry_run") === "1" });
    return Response.json(result);
  },
};

async function runMonitor(env, options = {}) {
  requireEnv(env, ["DATE_FROM", "DATE_TO"]);
  const rows = await fetchSlotRows();
  const matches = await findMatches(rows, env);
  const seen = await loadSeen(env);
  const newMatches = matches.filter((match) => !seen.has(match.fingerprint));

  const result = {
    fetchedRows: rows.length,
    matches: matches.length,
    newMatches: newMatches.length,
    checkedAt: new Date().toISOString(),
  };

  if (newMatches.length === 0) {
    console.log(JSON.stringify(result));
    return result;
  }

  const { subject, body } = formatMessage(newMatches, env);
  console.log(body);

  if (!options.dryRun) {
    await Promise.all([sendResendEmail(subject, body, env), sendServerChan(subject, body, env)]);
    for (const match of newMatches) {
      seen.add(match.fingerprint);
    }
    await saveSeen(env, seen);
  }

  return result;
}

function requireEnv(env, names) {
  const missing = names.filter((name) => !String(env[name] || "").trim());
  if (missing.length > 0) {
    throw new Error(`Missing required environment variable(s): ${missing.join(", ")}`);
  }
}

async function fetchSlotRows() {
  const query = new URLSearchParams({ select: "data,updated_at,city_key,visa_class" });
  const response = await fetch(`${SUPABASE_URL}/rest/v1/slot_data?${query}`, {
    headers: {
      apikey: SUPABASE_ANON_KEY,
      Authorization: `Bearer ${SUPABASE_ANON_KEY}`,
      Accept: "application/json",
    },
  });
  if (!response.ok) {
    throw new Error(`Supabase request failed: HTTP ${response.status}: ${await response.text()}`);
  }
  return response.json();
}

async function findMatches(rows, env) {
  const cities = csv(env.TARGET_CITIES);
  const visaTerms = csv(env.TARGET_VISA_TYPES);
  const start = parseDate(env.DATE_FROM, "DATE_FROM");
  const end = parseDate(env.DATE_TO, "DATE_TO");
  if (end < start) {
    throw new Error("DATE_TO must be on or after DATE_FROM");
  }

  const matches = [];
  for (const row of rows) {
    const data = row.data || {};
    const attrs = data.attrs || {};
    const visaClass = String(row.visa_class || attrs.visa_class || "");
    if (!cityMatches(row, cities) || !visaMatches(visaClass, visaTerms)) {
      continue;
    }

    const slots = data.slots || {};
    for (const slotDate of Object.keys(slots).sort()) {
      const current = parseDate(slotDate, `slot date ${slotDate}`);
      if (current < start || current > end) {
        continue;
      }

      const times = Array.isArray(slots[slotDate])
        ? slots[slotDate].map((item) => item && item.time).filter(Boolean).map(String)
        : [];
      const match = {
        cityKey: String(row.city_key || ""),
        cityName: getCityName(row),
        visaClass: cleanText(visaClass),
        slotDate,
        times,
        updatedAt: String(row.updated_at || ""),
      };
      match.fingerprint = await fingerprint(match);
      matches.push(match);
    }
  }
  return matches;
}

function csv(value) {
  return String(value || "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function normalize(value) {
  return String(value || "").toLowerCase().trim().replace(/\s+/g, " ");
}

function cleanText(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function parseDate(value, name) {
  const text = String(value || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw new Error(`${name} must use YYYY-MM-DD, got ${JSON.stringify(text)}`);
  }
  return text;
}

function getCityName(row) {
  const data = row.data || {};
  const name = String(data.visa_type_name || row.city_key || "");
  return name.includes(" - ") ? name.split(" - ").pop().trim() : name.trim();
}

function cityMatches(row, targets) {
  if (targets.length === 0) {
    return true;
  }
  const cityKey = String(row.city_key || "");
  const haystack = new Set([normalize(cityKey), normalize(getCityName(row))]);
  for (const alias of CITY_ALIASES[cityKey] || []) {
    haystack.add(normalize(alias));
  }
  return targets.some((target) => haystack.has(normalize(target)));
}

function visaMatches(visaClass, targets) {
  if (targets.length === 0) {
    return true;
  }
  const normalized = normalize(visaClass);
  return targets.some((target) => normalized.includes(normalize(target)));
}

async function fingerprint(match) {
  const raw = [match.cityKey, match.visaClass, match.slotDate, match.times.join(","), match.updatedAt].join("|");
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function loadSeen(env) {
  const raw = await env.VISA_MONITOR_STATE.get("seen");
  if (!raw) {
    return new Set();
  }
  try {
    return new Set(JSON.parse(raw).seen || []);
  } catch {
    return new Set();
  }
}

async function saveSeen(env, seen) {
  const payload = {
    updatedAt: new Date().toISOString(),
    seen: [...seen].slice(-1000),
  };
  await env.VISA_MONITOR_STATE.put("seen", JSON.stringify(payload));
}

function formatMessage(matches, env) {
  const subject = `US visa slot alert: ${matches.length} match(es)`;
  const lines = [
    subject,
    "",
    `Checked at: ${new Date().toISOString()}`,
    `Target cities: ${env.TARGET_CITIES || "any"}`,
    `Target visa types: ${env.TARGET_VISA_TYPES || "any"}`,
    `Date range: ${env.DATE_FROM} to ${env.DATE_TO}`,
    "",
  ];

  for (const match of matches.slice(0, 30)) {
    lines.push(
      `- ${match.slotDate} ${match.times.length ? match.times.join(", ") : "time not listed"}`,
      `  City: ${match.cityName} (${match.cityKey})`,
      `  Visa: ${match.visaClass}`,
      `  Updated: ${match.updatedAt}`,
      "",
    );
  }
  if (matches.length > 30) {
    lines.push(`...and ${matches.length - 30} more matches.`);
  }
  lines.push("Source: https://qmq.app/");
  return { subject, body: lines.join("\n") };
}

async function sendServerChan(subject, body, env) {
  if (!env.SERVERCHAN_SENDKEY) {
    console.log("ServerChan skipped: set SERVERCHAN_SENDKEY.");
    return;
  }
  const form = new URLSearchParams({ title: subject, desp: body });
  const response = await fetch(`https://sctapi.ftqq.com/${env.SERVERCHAN_SENDKEY}.send`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  if (!response.ok) {
    throw new Error(`ServerChan failed: HTTP ${response.status}: ${await response.text()}`);
  }
}

async function sendResendEmail(subject, body, env) {
  if (!env.RESEND_API_KEY || !env.EMAIL_FROM || !env.EMAIL_TO) {
    console.log("Email skipped: set RESEND_API_KEY, EMAIL_FROM, and EMAIL_TO.");
    return;
  }
  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: env.EMAIL_FROM,
      to: csv(env.EMAIL_TO),
      subject,
      text: body,
    }),
  });
  if (!response.ok) {
    throw new Error(`Resend email failed: HTTP ${response.status}: ${await response.text()}`);
  }
}
