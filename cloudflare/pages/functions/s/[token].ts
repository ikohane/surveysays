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
      pre.vignette { white-space: pre-wrap; background: #f3f4f6; padding: 12px; border-radius: 8px; }
      .qcard { background: white; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px; margin: 0 0 16px 0; }
      .qhdr { font-size: 12px; color: #6b7280; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; }
      .qprompt { margin-top: 6px; font-size: 15px; font-weight: 700; color: #111827; }
      .choices2 { display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 12px; }
      @media (min-width: 760px) { .choices2 { grid-template-columns: 1fr 1fr; } }
      .choiceCard { border: 2px solid #e5e7eb; border-radius: 12px; padding: 12px; background: #fff; cursor: pointer; position: relative; }
      .choiceCard:hover { border-color: #c7d2fe; }
      .choiceCard.selected { border-color: #2563eb; background: #eef2ff; box-shadow: 0 0 0 3px rgba(37,99,235,0.12); }
      .choiceTop { display:flex; justify-content: space-between; gap: 10px; align-items: center; }
      .choiceTitle { font-weight: 700; }
      .check { width: 18px; height: 18px; border-radius: 999px; border: 2px solid #9ca3af; display:flex; align-items:center; justify-content:center; font-size: 12px; color: transparent; }
      .choiceCard.selected .check { border-color: #2563eb; background: #2563eb; color: white; }
      .choiceVignette { margin-top: 10px; white-space: pre-wrap; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
      .hiddenRadio { position: absolute; opacity: 0; pointer-events: none; }
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

      const state = { qjson: null, layout: null };

      function normalizeLayout(cfg) {
        const d = cfg && typeof cfg === "object" ? cfg : {};
        return {
          promptFirst: d.promptFirst !== false,
          questionDemarcation: {
            style: (d.questionDemarcation && d.questionDemarcation.style) || "card",
            gapPx: (d.questionDemarcation && Number(d.questionDemarcation.gapPx)) || 16,
          },
          singleSelect: {
            layout: (d.singleSelect && d.singleSelect.layout) || "cards",
          },
        };
      }

      function splitPatientsFromVignette(text) {
        const s = String(text || "").trim();
        if (!s) return [""];
        // Split on blank lines. Typical data has 2 patients separated by a blank line.
        const parts = s.split(/\\n\\s*\\n/).map(p => p.trim()).filter(Boolean);
        return parts.length ? parts : [s];
      }

      function groupBlocks(blocks) {
        const groups = [];
        let vbuf = [];
        for (const b of blocks) {
          if (b.type === "vignette") {
            vbuf.push(String(b.text || ""));
            continue;
          }
          if (b.type === "singleSelect" || b.type === "freeText") {
            groups.push({ promptBlock: b, vignetteTexts: vbuf });
            vbuf = [];
            continue;
          }
        }
        // If trailing vignettes exist, render them as a final note card.
        if (vbuf.length) groups.push({ promptBlock: null, vignetteTexts: vbuf });
        return groups;
      }

      function renderSingleSelectCards(groupIdx, b, vignetteTexts) {
        const wrap = document.createElement("div");
        wrap.className = "choices2";
        const id = String(b.id);
        const choices = Array.isArray(b.choices) ? b.choices : [];
        const vignetteCombined = vignetteTexts.join("\\n\\n");
        const patientParts = splitPatientsFromVignette(vignetteCombined);

        const pickV = (i) => {
          if (patientParts.length >= 2) return patientParts[i] || "";
          return vignetteCombined;
        };

        const makeCard = (c, i) => {
          const cid = String(c.id);
          const card = document.createElement("label");
          card.className = "choiceCard";
          card.tabIndex = 0;
          card.setAttribute("role", "radio");
          card.setAttribute("aria-checked", "false");

          const radio = document.createElement("input");
          radio.className = "hiddenRadio";
          radio.type = "radio";
          radio.name = id;
          radio.value = cid;
          radio.required = !!b.required;

          const top = document.createElement("div");
          top.className = "choiceTop";
          const title = document.createElement("div");
          title.className = "choiceTitle";
          title.textContent = String(c.label || cid);
          const check = document.createElement("div");
          check.className = "check";
          check.textContent = "✓";
          top.appendChild(title);
          top.appendChild(check);

          const vig = document.createElement("div");
          vig.className = "choiceVignette";
          vig.textContent = pickV(i);

          const syncSelected = () => {
            const selected = radio.checked;
            card.classList.toggle("selected", selected);
            card.setAttribute("aria-checked", selected ? "true" : "false");
          };
          radio.addEventListener("change", () => {
            // clear sibling selection styling
            const all = wrap.querySelectorAll("label.choiceCard");
            for (const el of all) el.classList.remove("selected");
            const allRadios = wrap.querySelectorAll("input[type=radio]");
            for (const r of allRadios) {
              if (r !== radio) r.checked = false;
            }
            radio.checked = true;
            syncSelected();
          });
          card.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              radio.checked = true;
              radio.dispatchEvent(new Event("change", { bubbles: true }));
            }
          });

          card.appendChild(radio);
          card.appendChild(top);
          if (vig.textContent) card.appendChild(vig);
          wrap.appendChild(card);
        };

        // Card-style assumes 2 choices; fallback if not.
        if (choices.length === 2) {
          makeCard(choices[0], 0);
          makeCard(choices[1], 1);
          return wrap;
        }

        // Fallback: simple radios
        for (const c of choices) {
          const row = document.createElement("div");
          const radio = document.createElement("input");
          radio.type = "radio";
          radio.name = id;
          radio.value = String(c.id);
          radio.required = !!b.required;
          const lbl = document.createElement("span");
          lbl.textContent = " " + String(c.label || c.id);
          row.appendChild(radio);
          row.appendChild(lbl);
          wrap.appendChild(row);
        }
        return wrap;
      }

      function renderBlocks(qjson, layout) {
        elBlocks.innerHTML = "";
        const blocks = Array.isArray(qjson.blocks) ? qjson.blocks : [];
        const groups = groupBlocks(blocks);
        groups.forEach((g, idx) => {
          const qc = document.createElement("div");
          qc.className = "qcard";
          qc.style.marginBottom = String(layout.questionDemarcation.gapPx || 16) + "px";
          const hdr = document.createElement("div");
          hdr.className = "qhdr";
          hdr.textContent = g.promptBlock ? ("Question " + (idx + 1)) : "Note";
          qc.appendChild(hdr);

          if (g.promptBlock) {
            const b = g.promptBlock;
            const prompt = document.createElement("div");
            prompt.className = "qprompt";
            prompt.textContent = String(b.prompt || "");
            const vignetteCombined = g.vignetteTexts.join("\\n\\n");
            const pre = document.createElement("pre");
            pre.className = "vignette";
            pre.textContent = vignetteCombined;

            if (layout.promptFirst) {
              qc.appendChild(prompt);
              // for card layout we show vignette inside cards; keep as fallback note
            } else {
              if (vignetteCombined) qc.appendChild(pre);
              qc.appendChild(prompt);
            }

            if (b.type === "singleSelect") {
              if (layout.singleSelect.layout === "cards") {
                const cards = renderSingleSelectCards(idx, b, g.vignetteTexts);
                qc.appendChild(cards);
              } else {
                // fallback to old rendering (rows)
                const lbl = document.createElement("label");
                lbl.textContent = String(b.prompt || "");
                qc.appendChild(lbl);
              }
            } else if (b.type === "freeText") {
              const inp = document.createElement("input");
              inp.type = "text";
              inp.name = String(b.id);
              inp.required = !!b.required;
              qc.appendChild(inp);
            }
          } else {
            // trailing vignette(s)
            const pre = document.createElement("pre");
            pre.className = "vignette";
            pre.textContent = g.vignetteTexts.join("\\n\\n");
            qc.appendChild(pre);
          }
          elBlocks.appendChild(qc);
        });
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
        state.layout = normalizeLayout(data.layoutConfig);
        elTitle.textContent = String(state.qjson.title || "Survey");
        elSubtitle.textContent = "Token: " + TOKEN;
        renderBlocks(state.qjson, state.layout);
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


