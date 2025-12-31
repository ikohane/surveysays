import { Env } from "../_lib/env";

function html(token: string): string {
  // Minimal vanilla JS renderer (no external deps).
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Survey</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; background: #f6f7fb; color: #111; }
      main { max-width: 820px; margin: 18px auto; padding: 0 18px 36px 18px; }
      .card { background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin-bottom: 14px; }
      h1 { margin: 0; font-size: 20px; }
      .muted { color: #6b7280; font-size: 13px; }
      label { display: block; font-size: 14px; margin: 8px 0 4px; }
      input[type="text"] { width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 8px; }
      button { padding: 10px 12px; border: 0; border-radius: 8px; background: #2563eb; color: white; font-weight: 600; cursor: pointer; }
      button:disabled { background: #9ca3af; cursor: not-allowed; }
      .err { background: #fee2e2; border: 1px solid #fecaca; color: #7f1d1d; padding: 10px 12px; border-radius: 8px; }
      .ok { background: #dcfce7; border: 1px solid #bbf7d0; color: #14532d; padding: 10px 12px; border-radius: 8px; }
      .choice { margin: 6px 0; }
      pre.vignette { white-space: pre-wrap; background: #f3f4f6; padding: 12px; border-radius: 8px; }
    </style>
  </head>
  <body>
    <main>
      <div class="card">
        <h1 id="title">Loading…</h1>
        <div class="muted" id="subtitle"></div>
      </div>

      <div id="flash"></div>

      <form id="form" class="card" style="display:none;">
        <div id="blocks"></div>
        <div style="height:12px;"></div>
        <button id="submitBtn" type="submit">Submit</button>
        <div class="muted" style="margin-top:10px;">One-and-done: resubmitting will return HTTP 409.</div>
      </form>
    </main>

    <script>
      const TOKEN = ${JSON.stringify(token)};
      const flash = (kind, msg) => {
        const el = document.getElementById("flash");
        el.innerHTML = "";
        const div = document.createElement("div");
        div.className = kind === "ok" ? "ok" : "err";
        div.textContent = msg;
        el.appendChild(div);
      };

      const elTitle = document.getElementById("title");
      const elSubtitle = document.getElementById("subtitle");
      const elForm = document.getElementById("form");
      const elBlocks = document.getElementById("blocks");
      const elSubmitBtn = document.getElementById("submitBtn");

      const state = { qjson: null };

      function renderBlocks(qjson) {
        elBlocks.innerHTML = "";
        const blocks = Array.isArray(qjson.blocks) ? qjson.blocks : [];
        for (const b of blocks) {
          if (b.type === "vignette") {
            const pre = document.createElement("pre");
            pre.className = "vignette";
            pre.textContent = String(b.text || "");
            elBlocks.appendChild(pre);
          } else if (b.type === "singleSelect") {
            const wrap = document.createElement("div");
            const prompt = document.createElement("label");
            prompt.textContent = String(b.prompt || "");
            wrap.appendChild(prompt);
            const choices = Array.isArray(b.choices) ? b.choices : [];
            for (const c of choices) {
              const row = document.createElement("div");
              row.className = "choice";
              const id = String(b.id);
              const cid = String(c.id);
              const radio = document.createElement("input");
              radio.type = "radio";
              radio.name = id;
              radio.value = cid;
              radio.required = !!b.required;
              const lbl = document.createElement("span");
              lbl.textContent = " " + String(c.label || cid);
              row.appendChild(radio);
              row.appendChild(lbl);
              wrap.appendChild(row);
            }
            elBlocks.appendChild(wrap);
          } else if (b.type === "freeText") {
            const wrap = document.createElement("div");
            const lbl = document.createElement("label");
            lbl.textContent = String(b.prompt || "");
            const inp = document.createElement("input");
            inp.type = "text";
            inp.name = String(b.id);
            inp.required = !!b.required;
            wrap.appendChild(lbl);
            wrap.appendChild(inp);
            elBlocks.appendChild(wrap);
          }
        }
      }

      async function loadSurvey() {
        const resp = await fetch("/api/survey/" + encodeURIComponent(TOKEN), { headers: { "accept": "application/json" }});
        if (!resp.ok) {
          flash("err", "Survey link not found or expired.");
          elTitle.textContent = "Survey";
          return;
        }
        const data = await resp.json();
        state.qjson = data.questionnaireJson;
        elTitle.textContent = String(state.qjson.title || "Survey");
        elSubtitle.textContent = "Token: " + TOKEN;
        renderBlocks(state.qjson);
        elForm.style.display = "block";
      }

      elForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        flash("ok", "Submitting…");
        elSubmitBtn.disabled = true;
        try {
          const fd = new FormData(elForm);
          const answers = {};
          for (const [k, v] of fd.entries()) {
            answers[k] = String(v);
          }
          const resp = await fetch("/api/submit/" + encodeURIComponent(TOKEN), {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ answers }),
          });
          if (resp.status === 409) {
            flash("err", "This survey was already submitted.");
            return;
          }
          if (!resp.ok) {
            const t = await resp.text();
            flash("err", "Submit failed: " + t);
            return;
          }
          flash("ok", "Thanks — your response was submitted.");
          elForm.style.display = "none";
        } finally {
          elSubmitBtn.disabled = false;
        }
      });

      loadSurvey().catch((e) => flash("err", "Error: " + String(e)));
    </script>
  </body>
</html>`;
}

export async function onRequest(context: { params: { token: string }; env: Env }): Promise<Response> {
  const token = String(context.params?.token || "").trim();
  if (!token) return new Response("Missing token", { status: 400 });
  return new Response(html(token), {
    status: 200,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}


