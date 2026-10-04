#!/usr/bin/env python3
"""Generates the three Stage 2 diagrams as SVG (website) and the same content
as draw.io-importable XML (so the team can edit them by hand).

    python3 docs/diagrams/make_diagrams.py

Figure 1  AS-IS swimlane          — actors, systems, delays, bottlenecks, exceptions
Figure 2  TO-BE agentic swimlane  — every activity badged A / H / E
Figure 3  Technical & governance architecture
"""
import html, os, xml.sax.saxutils as sx

HERE = os.path.dirname(os.path.abspath(__file__))

NAVY, INK, MUTED, LINE, BG = "#0f2744", "#13203a", "#5d6b85", "#dfe5ee", "#ffffff"
A_FG, A_BG = "#0d8a5f", "#e6f5ee"      # Autonomous
H_FG, H_BG = "#1d61b8", "#e7f0fb"      # Human in the loop
E_FG, E_BG = "#c0392b", "#fdecea"      # Escalation / exception
W_FG, W_BG = "#9a6700", "#fff7e0"      # delay / bottleneck
LANE_BG = "#f7f9fc"

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif")


def esc(s):
    return sx.escape(str(s))


def wrap(text, width):
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines


# Average glyph advance for this font stack, as a fraction of font size. Used to
# estimate how many characters fit, so labels wrap instead of spilling.
CHAR_W = 0.545


def box(x, y, w, h, text, fill, stroke, fg, badge=None, sub=None, rx=7, fs=11):
    # A badge sits in the top-right corner, so the text column is narrower there.
    usable = w - 16 - (26 if badge else 0)
    lines = wrap(text, max(9, int(usable / (fs * CHAR_W))))
    total = len(lines) * (fs + 2.5) + (11 if sub else 0)
    ty = y + h / 2 - total / 2 + fs
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
           f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>']
    for i, ln in enumerate(lines):
        out.append(f'<text x="{x + w/2}" y="{ty + i*(fs+2.5)}" font-family="{FONT}" '
                   f'font-size="{fs}" fill="{fg}" text-anchor="middle">{esc(ln)}</text>')
    if sub:
        for j, sl in enumerate(wrap(sub, max(10, int((w - 12) / (9 * CHAR_W))))[:2]):
            out.append(f'<text x="{x + w/2}" y="{ty + len(lines)*(fs+2.5) + 2 + j*10}" '
                       f'font-family="{FONT}" font-size="9" fill="{MUTED}" '
                       f'text-anchor="middle">{esc(sl)}</text>')
    if badge:
        bg, fgc = {"A": (A_BG, A_FG), "H": (H_BG, H_FG), "E": (E_BG, E_FG)}[badge]
        out.append(f'<rect x="{x + w - 25}" y="{y + 6}" width="19" height="16" rx="4" '
                   f'fill="{bg}" stroke="{fgc}" stroke-width="1"/>')
        out.append(f'<text x="{x + w - 15.5}" y="{y + 17.5}" font-family="{FONT}" '
                   f'font-size="10" font-weight="700" fill="{fgc}" '
                   f'text-anchor="middle">{badge}</text>')
    return "".join(out)


def arrow(x1, y1, x2, y2, colour=MUTED, dashed=False, label=None):
    d = ' stroke-dasharray="4 3"' if dashed else ""
    out = [f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{colour}" stroke-width="1.5" '
           f'fill="none" marker-end="url(#ar)"{d}/>']
    if label:
        out.append(f'<text x="{(x1+x2)/2}" y="{(y1+y2)/2 - 5}" font-family="{FONT}" '
                   f'font-size="9" fill="{MUTED}" text-anchor="middle">{esc(label)}</text>')
    return "".join(out)


def lane(x, y, w, h, title, i):
    fill = LANE_BG if i % 2 == 0 else "#ffffff"
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" '
            f'stroke="{LINE}" stroke-width="1"/>'
            f'<rect x="{x}" y="{y}" width="150" height="{h}" fill="{NAVY}"/>'
            + "".join(
                f'<text x="{x+75}" y="{y + h/2 - (len(wrap(title,18))-1)*6 + i2*12}" '
                f'font-family="{FONT}" font-size="10.5" font-weight="600" fill="#ffffff" '
                f'text-anchor="middle">{esc(ln)}</text>'
                for i2, ln in enumerate(wrap(title, 18))))


def svg(width, height, body, title, subtitle):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" font-family="{FONT}">
  <defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6"
      markerHeight="6" orient="auto-start-reverse">
      <path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>
  <rect width="{width}" height="{height}" fill="{BG}"/>
  <text x="24" y="32" font-size="17" font-weight="700" fill="{INK}">{esc(title)}</text>
  <text x="24" y="52" font-size="11.5" fill="{MUTED}">{esc(subtitle)}</text>
  {body}
</svg>'''


def legend(x, y, entries):
    out, cx = [], x
    for label, fill, stroke, fg in entries:
        out.append(f'<rect x="{cx}" y="{y}" width="13" height="13" rx="3" fill="{fill}" '
                   f'stroke="{stroke}" stroke-width="1.2"/>')
        out.append(f'<text x="{cx+19}" y="{y+10.5}" font-size="10" fill="{MUTED}">{esc(label)}</text>')
        cx += 26 + len(label) * 5.6
    return "".join(out)


# ---------------------------------------------------------------- Figure 1
def figure1():
    LANES = ["Customer", "Fraud rule engine", "L1 analyst", "Contact centre",
             "Core banking ops", "L2 investigator", "Compliance / AML"]
    # (lane, column, label, kind, sub)  kind: plain | delay | exception | decision
    STEPS = [
        (1, 0, "Raise alert on transaction", "delay", "high false-positive volume"),
        (2, 1, "Pick alert from queue", "delay", "queue wait at peaks"),
        (2, 2, "Review transaction history", "plain", "core banking"),
        (2, 3, "Review customer profile", "plain", "CRM / KYC"),
        (2, 4, "Review device & login data", "delay", "separate access request"),
        (2, 5, "Check beneficiary & watchlist", "plain", "manual lookups"),
        (2, 6, "Decide outcome", "decision", "judgement varies by analyst"),
        (3, 7, "Call customer to verify", "delay", "call-back queue"),
        (4, 8, "Place / release hold", "delay", "e-mail request"),
        (5, 9, "Review escalated case", "exception", "evidence re-gathered"),
        (6, 10, "Mule / SAR referral", "plain", "manual"),
        (2, 11, "Write free-text notes and close", "exception", "audit gaps"),
    ]
    LANE_H, TOP, X0, COL_W, BOX_W, BOX_H = 92, 92, 24, 152, 136, 72
    W = X0 + 150 + 12 * COL_W + 40
    H = TOP + len(LANES) * LANE_H + 78

    body = [lane(X0, TOP + i * LANE_H, W - X0 - 24, LANE_H, t, i)
            for i, t in enumerate(LANES)]

    pos = {}
    for lane_i, col, label, kind, sub in STEPS:
        x = X0 + 150 + 14 + col * COL_W
        y = TOP + lane_i * LANE_H + (LANE_H - BOX_H) / 2
        fill, stroke, fg = {
            "plain": ("#ffffff", LINE, INK),
            "delay": (W_BG, "#e8d08a", W_FG),
            "exception": (E_BG, "#f3c0ba", E_FG),
            "decision": ("#eef3fb", "#b9cbe6", NAVY),
        }[kind]
        body.append(box(x, y, BOX_W, BOX_H, label, fill, stroke, fg, sub=sub, fs=10.5))
        pos[col] = (x, y, BOX_W, BOX_H)

    for a, b in zip(range(11), range(1, 12)):
        (x1, y1, w1, h1), (x2, y2, _, h2) = pos[a], pos[b]
        body.append(arrow(x1 + w1, y1 + h1 / 2, x2 - 3, y2 + h2 / 2))

    body.append(f'<text x="{X0}" y="{H-46}" font-size="10.5" font-weight="600" '
                f'fill="{INK}">Bottlenecks: evidence sits in five separate systems; '
                f'every transition above is a manual handoff.</text>')
    body.append(legend(X0, H - 32, [
        ("activity", "#ffffff", LINE, INK),
        ("delay / bottleneck", W_BG, "#e8d08a", W_FG),
        ("exception", E_BG, "#f3c0ba", E_FG),
        ("decision point", "#eef3fb", "#b9cbe6", NAVY)]))

    return svg(W, H, "".join(body), "Figure 1 — AS-IS fraud alert investigation",
               "Manual, sequential, evidence spread across five systems. "
               "Median handling time is dominated by evidence gathering, not by judgement.")


# ---------------------------------------------------------------- Figure 2
def figure2():
    LANES = ["UI (investigator)", "Orchestrator (n8n)", "Specialised agents",
             "Tools / data", "Human investigator", "L2 / escalation", "Action & audit"]
    STEPS = [
        (0, 0, "Trigger investigation", "H", "dashboard -> webhook"),
        (1, 1, "Validate payload, mask PII", "A", "input guardrail + injection scan"),
        (3, 2, "Fetch evidence, compute features", "A", "Supabase + hard-flag rules"),
        (1, 3, "Route by alert type, flags, completeness", "A", "switch"),
        (2, 4, "Transaction Analysis Agent", "A", "amount, velocity, splits"),
        (2, 5, "Customer Behaviour Agent", "A", "device, SIM, geo, payee"),
        (2, 6, "Risk/Policy Agent", "A", "policy + watchlist tools"),
        (2, 7, "Recommendation Agent", "A", "Proceed/Verify/Hold/Escalate"),
        (1, 8, "Validate output", "A", "schema, grounding, numbers"),
        (6, 9, "Auto-Proceed", "A", "low risk; 10% sampled"),
        (4, 9, "Approve or override", "H", "Wait node resumes"),
        (5, 9, "Hold + escalate to L2", "E", "hard flag or high risk"),
        (1, 10, "Exception queue", "E", "injection, bad output, missing data"),
        (6, 11, "Execute outcome", "A", "mock CBS + audit_log"),
    ]
    LANE_H, TOP, X0, COL_W, BOX_W, BOX_H = 104, 96, 24, 164, 146, 84
    W = X0 + 150 + 12 * COL_W + 40
    H = TOP + len(LANES) * LANE_H + 82

    body = [lane(X0, TOP + i * LANE_H, W - X0 - 24, LANE_H, t, i)
            for i, t in enumerate(LANES)]
    pos = {}
    for lane_i, col, label, ahe, sub in STEPS:
        x = X0 + 150 + 14 + col * COL_W
        y = TOP + lane_i * LANE_H + (LANE_H - BOX_H) / 2
        fill, stroke, fg = {"A": (A_BG, A_FG, A_FG), "H": (H_BG, H_FG, H_FG),
                            "E": (E_BG, E_FG, E_FG)}[ahe]
        body.append(box(x, y, BOX_W, BOX_H, label, fill, stroke, INK, badge=ahe, sub=sub, fs=10.5))
        pos.setdefault(col, []).append((x, y, BOX_W, BOX_H))

    for a in range(11):
        for (x1, y1, w1, h1) in pos.get(a, []):
            for (x2, y2, _, h2) in pos.get(a + 1, []):
                body.append(arrow(x1 + w1, y1 + h1 / 2, x2 - 3, y2 + h2 / 2))

    body.append(f'<text x="{X0}" y="{H-48}" font-size="10.5" font-weight="600" fill="{INK}">'
                f'Agents hold no action tools. Only the post-approval branch and the '
                f'deterministic hard-flag branch can touch an account.</text>')
    body.append(legend(X0, H - 34, [
        ("A — autonomous", A_BG, A_FG, A_FG),
        ("H — human in the loop", H_BG, H_FG, H_FG),
        ("E — escalation / exception", E_BG, E_FG, E_FG)]))

    return svg(W, H, "".join(body), "Figure 2 — TO-BE governed agentic process",
               "Every activity is badged A (autonomous), H (human-in-the-loop) or "
               "E (escalation). A model may escalate a hard flag further; it can never clear one.")


# ---------------------------------------------------------------- Figure 3
def figure3():
    LAYERS = [
        ("UI / UX", "#eef3fb", NAVY, [
            ("Alert queue", "trigger + model selector"),
            ("Case view", "evidence, agents, risk gauge"),
            ("Escalation & exception queues", "L2 and failure paths"),
            ("Audit trail & benchmark", "every step, every model"),
        ], "Login, role-based screens, mandatory override reason"),
        ("Workflow / orchestration", "#eef6f1", A_FG, [
            ("n8n main workflow", "20 nodes, 1 per guide 6.4"),
            ("Error workflow", "any failure -> exception case"),
            ("Mock core banking", "release / verify / hold / escalate"),
        ], "Deterministic routing thresholds, retry limit of 1, Wait-node approval gate"),
        ("Agents / LLMs", "#fdf4ea", W_FG, [
            ("Transaction Analysis", "amount, velocity, splits"),
            ("Customer Behaviour", "device, SIM, geo, payee"),
            ("Risk / Policy", "policy + watchlist"),
            ("Recommendation", "outcome + rationale"),
        ], "Versioned prompts, temperature 0, JSON-only output, NO action tools"),
        ("Tools / data", "#f3f0fa", "#4a3aa7", [
            ("Supabase Postgres", "synthetic data, RLS"),
            ("Policy rules & thresholds", "admin-editable"),
            ("Mule watchlist", "1930 / internal"),
            ("Mock core banking API", "post-approval only"),
        ], "PII masked before any model call, least-privilege service keys"),
        ("Guardrails", "#fdecea", E_FG, [
            ("Input guardrail", "schema, masking, injection scan"),
            ("Hard-flag rules", "HF1-HF3, never downgradable"),
            ("Output validator", "schema, grounding, numbers"),
            ("Action gate", "approval matrix"),
        ], "Risk -> Guardrail -> NIST AI RMF requirement -> implementation (docs/governance)"),
        ("Human oversight & audit", "#e7f0fb", H_FG, [
            ("L1 / L2 / admin roles", "approval matrix"),
            ("10% sampling of auto-decisions", "against automation bias"),
            ("SLA timeout", "auto-escalate, never drop"),
            ("audit_log", "append-only, attributable"),
        ], "Human decision authority on every non-trivial case"),
    ]
    X0, TOP, LAYER_H, W = 24, 96, 112, 1180
    H = TOP + len(LAYERS) * LAYER_H + 46
    body = []
    for i, (name, fill, fg, items, note) in enumerate(LAYERS):
        y = TOP + i * LAYER_H
        body.append(f'<rect x="{X0}" y="{y}" width="{W-2*X0}" height="{LAYER_H-10}" rx="9" '
                    f'fill="{fill}" stroke="{LINE}" stroke-width="1.2"/>')
        body.append(f'<text x="{X0+16}" y="{y+24}" font-size="12" font-weight="700" '
                    f'fill="{fg}">{esc(name)}</text>')
        bw = (W - 2 * X0 - 32 - (len(items) - 1) * 10) / len(items)
        for j, (label, sub) in enumerate(items):
            bx = X0 + 16 + j * (bw + 10)
            body.append(box(bx, y + 34, bw, 42, label, "#ffffff", LINE, INK, sub=sub, fs=10))
        body.append(f'<text x="{X0+16}" y="{y+LAYER_H-18}" font-size="9.5" fill="{MUTED}">'
                    f'Governance control: {esc(note)}</text>')
        if i < len(LAYERS) - 1:
            body.append(arrow(W / 2, y + LAYER_H - 10, W / 2, y + LAYER_H - 1))

    return svg(W, H, "".join(body),
               "Figure 3 — Technical and governance architecture",
               "Each layer carries its own control. Governance is a property of the "
               "architecture, not a document bolted on beside it.")


# ---------------------------------------------------------------- draw.io
def drawio(figs):
    """Wrap each SVG as an editable draw.io page containing the image plus a
    note, so the team can annotate without regenerating."""
    pages = []
    for name, _ in figs:
        pages.append(f"""  <diagram name="{esc(name)}">
    <mxGraphModel dx="1100" dy="800" grid="1" gridSize="10" page="1"
        pageWidth="1600" pageHeight="1100" math="0" shadow="0">
      <root>
        <mxCell id="0"/><mxCell id="1" parent="0"/>
        <mxCell id="n" value="Import {esc(name)}.svg here (File &gt; Import) to edit, or edit docs/diagrams/make_diagrams.py and regenerate."
          style="text;html=1;align=left;verticalAlign=top;fontSize=13;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="760" height="60" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>""")
    return "<mxfile host=\"app.diagrams.net\">\n" + "\n".join(pages) + "\n</mxfile>"


if __name__ == "__main__":
    figs = [("figure-1-as-is", figure1()),
            ("figure-2-to-be", figure2()),
            ("figure-3-architecture", figure3())]
    for name, content in figs:
        with open(os.path.join(HERE, name + ".svg"), "w") as f:
            f.write(content)
        print(f"  {name}.svg  ({len(content):,} bytes)")
    with open(os.path.join(HERE, "fraudsentinel.drawio"), "w") as f:
        f.write(drawio(figs))
    print("  fraudsentinel.drawio")
