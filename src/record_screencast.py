#!/usr/bin/env python3
"""Silent screencast for the demo, following docs/demo_script.md (2026-07-18).

Records the live viewer (localhost:8899, completed-games farm) with an injected
virtual cursor. One continuous take, nine marked segments (eight narrated cues
plus a silent closing URL card), target <= 2:30.

Subtitles are NOT burned in: this script only records video + a virtual cursor
and writes segment timings to ``<OUT_DIR>/marks.json``. Captions live in a
separate SRT built from those timings (``build_srt.py``; empty-text segments —
the closing card — produce no cue). The ``mafiascope_tour_done`` localStorage
flag is set before load so the onboarding tour never covers a scene.

Pacing: each scene's length is tuned to its TTS narration (piper lessac,
length_scale 1.08) plus a <=1.5 s tail, so the optional voice track neither
rushes nor leaves dead air. Segments:
  1. overview (36594b66, step 16)                      ~17.3s (tts 16.1)
  2. timeline + deception rings                        ~17.2s (tts 16.1)
  3. metrics strip                                     ~15.5s (tts 14.3)
  4. calibration (corpus view, no agent switching)     ~13.5s (tts 12.5)
  5. impersonate Logan + copy-link                     ~19.3s (tts 18.1)
  6. bifurcation point -> fork fan -> flip fork        ~31.6s (tts 30.6)
  7. game family (resistance, transcript-only)         ~11.3s (tts 10.2)
  8. dashboard facets + light-theme flip               ~11.4s (tts 10.2)
  9. closing URL card overlay (silent, no cue)          ~5.0s
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8899"
GAME = "36594b66"                                        # featured EN game, Mafia wins
BIF_GAME = "3845221c-d963-4e1b-b34c-6c857979a1f0"        # bifurcation parent (policy gap @R1.11)
BIF_STEP = 17                                            # step idx of R1.11 (Alex's vote)
FORK_FLIP = "eae8e206-c2d3-4e0c-b66c-2a9f9609a1f6"       # variant #2 fork, Villagers win
RES_GAME = "4a9a8dc9-0ea4-4469-bb47-426a2226e79e"        # resistance, Spy wins
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else "video_raw"
W, H = 1680, 940

# Virtual cursor only — no caption bar (subtitles ship as a separate SRT).
# Node hover is disabled: the Big Five tooltip must not appear (the B5 line was
# dropped from the paper), and stray tooltips over the GT graph break scenes.
INJECT = """
(() => {
  if (document.getElementById('__cur')) return;
  const c = document.createElement('div');
  c.id = '__cur';
  c.style.cssText = `position:fixed;z-index:99999;width:18px;height:18px;
    border-radius:50%;background:rgba(255,255,255,.35);border:2px solid rgba(255,255,255,.9);
    box-shadow:0 0 6px rgba(0,0,0,.6);pointer-events:none;left:-40px;top:-40px;
    transition:left .05s linear, top .05s linear;`;
  document.body.appendChild(c);
  window.addEventListener('mousemove', e => {
    c.style.left = (e.clientX - 9) + 'px';
    c.style.top = (e.clientY - 9) + 'px';
  }, true);
  const st = document.createElement('style');
  st.id = '__nohover';
  st.textContent = '.node{pointer-events:none}';
  document.head.appendChild(st);
})();
"""

# Closing card: repo + live-demo URLs ON SCREEN (they are not narrated).
# Styled on the viewer's warm-dark tokens; readable over the light dashboard.
URL_CARD = """
(() => {
  if (document.getElementById('__urlcard')) return;
  const c = document.createElement('div');
  c.id = '__urlcard';
  c.style.cssText = `position:fixed;inset:0;z-index:99998;display:flex;
    align-items:center;justify-content:center;background:rgba(13,12,10,.55);
    opacity:0;transition:opacity .6s ease;`;
  c.innerHTML = `<div style="background:rgba(24,23,20,.95);
      border:1px solid rgba(255,255,255,.14);border-radius:16px;
      padding:36px 56px;text-align:center;box-shadow:0 24px 64px rgba(0,0,0,.5);
      font-family:'Onest',system-ui,sans-serif;">
    <div style="font-size:15px;font-weight:600;color:#fff052;
        letter-spacing:.05em;margin-bottom:16px">MafiaScope — open source</div>
    <div style="font-size:30px;font-weight:600;color:#f5f5f2;
        margin:8px 0">github.com/karpovilia/mafiascope</div>
    <div style="font-size:30px;font-weight:600;color:#f5f5f2;
        margin:8px 0">karpovilia.github.io/mafiascope</div>
    <div style="font-size:13px;color:#9f9e9b;margin-top:14px">live demo — runs in your browser</div>
  </div>`;
  document.body.appendChild(c);
  requestAnimationFrame(() => { c.style.opacity = '1'; });
})();
"""

# Subtitle text per segment (single source of truth for the SRT and the TTS
# track). Segment 9 (URL card) is silent: empty text -> no SRT cue, no audio.
# ~350 words total, simple English, no idioms; URLs on screen only.
CAPS = [
    "This is MafiaScope: seven language-model agents playing Mafia. The center shows ground "
    "truth. Every side panel is one agent's private beliefs, collected by structured probes "
    "after every public message, outside the game context, so the game itself is never disturbed.",

    "The timeline replays the game step by step, and beliefs update live. On the ground-truth "
    "graph every mafioso carries a deception ring: the share of informed observers who still "
    "fail to name it Mafia. Here Finley stays fully hidden, while Logan is already partly exposed.",

    "The Metrics panel turns the viewer into a measuring instrument: first-order accuracy "
    "against ground truth, crowd Mafia recall versus per-mafioso deception success, and "
    "second-order consistency: does an agent know what others think of it?",

    "Calibration compares stated confidence with actual accuracy. These agents are "
    "overconfident: across the corpus, answers given at about eighty-five percent confidence "
    "are right only about fifty-five percent of the time.",

    "Click a player to impersonate them: this is Logan, secretly Mafia. The right side is "
    "pure theory of mind: what Logan believes others think of him, against what they actually "
    "think. The match score makes the gap quantitative: Logan gets only two of four right. "
    "And one click copies a deep link to this exact view.",

    "The core feature: bifurcation points. A colored badge marks a decisive vote; this agent read "
    "the board correctly and still voted wrong. That vote was resampled five hundred times, "
    "and twenty diverse variants were replayed to the end. The panel shows lock-in and flip "
    "share: four of twenty forks flip the game to a Villagers win. Flips concentrate where "
    "the agent had read the game right; wrongly-assessed votes are locked in. One click opens "
    "a flipped fork: here the village survives, and each analysed loss gets a classified cause.",

    "The same engine and viewer cover a whole game family: werewolf skins and Resistance. "
    "Transcript-only games keep the timeline and the replay, without belief panels.",

    "The cross-game dashboard aggregates these metrics over the corpus, filtered by language "
    "and model family. MafiaScope is open source, and the full demo runs in your browser.",

    "",   # 9: closing URL card — on-screen only, no narration, no cue
]


def sweep(page, points, dur):
    """Move the mouse through points over roughly dur seconds."""
    step_t = dur / max(1, len(points))
    for x, y in points:
        page.mouse.move(x, y, steps=25)
        time.sleep(step_t)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(
            viewport={"width": W, "height": H},
            record_video_dir=OUT_DIR,
            record_video_size={"width": W, "height": H},
        )
        # Suppress the onboarding tour on every navigation (viewer + dashboard).
        ctx.add_init_script(
            "try{localStorage.setItem('mafiascope_tour_done','1')}catch(e){}")
        page = ctx.new_page()
        t0 = time.time()
        segments = []
        state = {"trim": None}

        def now():
            return round(time.time() - t0, 2)

        def mark(name):
            print(f"[{now():6.1f}s] {name}", flush=True)

        def hide_hint():
            page.evaluate(
                "() => { const h = document.getElementById('hint');"
                " if (h) h.style.display = 'none'; }")

        page.goto(f"{BASE}/viewer.html#g={GAME}&s=16")
        page.wait_for_selector("#main-grid svg", timeout=60000)
        time.sleep(2.5)  # let layout settle; trimmed from the final cut
        page.evaluate(INJECT)
        state["trim"] = now()
        mark("loaded (trim point)")

        # --- 1. overview, ~17.3s (tts 16.1) ---
        s = now()
        sweep(page, [(W//2, H//2 - 60), (W//2 - 380, H//2 - 200), (W//2 + 380, H//2 - 200),
                     (W//2 + 380, H//2 + 220), (W//2 - 380, H//2 + 220), (W//2, 120)], 12.5)
        time.sleep(1.8)
        segments.append({"i": 1, "start": s, "end": now()})
        mark("seg1 done")

        # --- 2. timeline steps + deception rings, ~17.2s (tts 16.1) ---
        s = now()
        for st in (17, 18, 19):
            page.evaluate(f"setStep({st})")
            time.sleep(2.0)
        # point at the two deception rings on the GT graph (no node hover:
        # pointer events are off, so no tooltip can appear)
        sweep(page, [(W//2 - 150, 240), (W//2 + 150, 240), (W//2 + 60, 330)], 8.5)
        time.sleep(1.2)
        segments.append({"i": 2, "start": s, "end": now()})
        mark("seg2 done")

        # --- 3. metrics strip, ~15.5s (tts 14.3) ---
        s = now()
        page.evaluate("toggleMetrics()")
        page.evaluate("setStep(26)")
        hide_hint()
        time.sleep(1.5)
        panel = page.locator("#metrics-panel")
        box = panel.bounding_box()
        if box:
            y = box["y"] + box["height"] * 0.55
            sweep(page, [(box["x"] + box["width"] * f, y) for f in (0.18, 0.5, 0.82)], 10.4)
        time.sleep(2.2)
        segments.append({"i": 3, "start": s, "end": now()})
        mark("seg3 done")

        # --- 4. calibration: corpus view only, no agent switching, ~13.5s (tts 12.5) ---
        s = now()
        page.evaluate("toggleMetrics()")
        page.evaluate("openCalibration()")
        hide_hint()
        time.sleep(2.2)
        overlay = page.locator("#calib-modal").bounding_box()
        if overlay:
            # slow pass: 60-79 bin -> 80-100 bin -> along the diagonal
            sweep(page, [(overlay["x"] + overlay["width"] * 0.62, overlay["y"] + overlay["height"] * 0.60),
                         (overlay["x"] + overlay["width"] * 0.82, overlay["y"] + overlay["height"] * 0.50),
                         (overlay["x"] + overlay["width"] * 0.50, overlay["y"] + overlay["height"] * 0.62)], 7.8)
        time.sleep(2.2)
        segments.append({"i": 4, "start": s, "end": now()})
        mark("seg4 done")

        # --- 5. impersonate Logan + copy link, ~19.3s (tts 18.1) ---
        s = now()
        page.evaluate("closeCalibration()")
        page.evaluate("setStep(20)")
        page.evaluate("enterImpersonate('Logan')")
        hide_hint()
        time.sleep(2.2)
        sweep(page, [(W//2, H//2), (W - 340, H//2 - 220), (W - 340, H//2 + 40),
                     (W - 340, H//2 + 260)], 9.0)
        # match score chip on the BELIEVED panel title
        sc = page.locator("#imp-believed-score").bounding_box()
        if sc:
            page.mouse.move(sc["x"] + sc["width"]/2, sc["y"] + sc["height"]/2, steps=25)
        time.sleep(1.8)
        # one-click shareable deep link (#g/s/a/m) — real click, toast appears
        btn = page.locator("#copy-link-btn")
        bb = btn.bounding_box()
        if bb:
            page.mouse.move(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2, steps=25)
            page.mouse.click(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2)
        time.sleep(3.3)
        segments.append({"i": 5, "start": s, "end": now()})
        mark("seg5 done")

        # --- 6. bifurcation point -> fork fan -> flip fork, ~31.6s (tts 30.6) ---
        s = now()
        page.evaluate("exitImpersonate()")
        page.evaluate(f"switchGame('{BIF_GAME}')")
        page.evaluate(f"setStep({BIF_STEP})")
        hide_hint()
        time.sleep(2.0)
        # quadrant badge on the timeline
        bb = page.locator("#bif-badge").bounding_box()
        if bb:
            page.mouse.move(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2, steps=25)
            time.sleep(2.0)
            page.mouse.click(bb["x"] + bb["width"]/2, bb["y"] + bb["height"]/2)
        else:
            page.evaluate("openBifPanelCurrent()")
        time.sleep(3.0)
        # lock-in / flip-share summary, then the fan of 20 variants
        summ = page.locator("#bif-summary").bounding_box()
        if summ:
            sweep(page, [(summ["x"] + summ["width"]*0.3, summ["y"] + summ["height"]*0.3),
                         (summ["x"] + summ["width"]*0.3, summ["y"] + summ["height"]*0.8)], 4.0)
        lst = page.locator("#bif-variants").bounding_box()
        if lst:
            page.mouse.move(lst["x"] + lst["width"]*0.5, lst["y"] + lst["height"]*0.4, steps=25)
            page.mouse.wheel(0, 260)
            time.sleep(2.5)
            page.mouse.wheel(0, -260)
            time.sleep(1.5)
        # click the first flip variant (variant #2 -> Villagers-win fork)
        flip = page.locator(".bif-variant.flip").first
        fb = flip.bounding_box()
        if fb:
            page.mouse.move(fb["x"] + fb["width"]*0.4, fb["y"] + fb["height"]/2, steps=25)
            time.sleep(1.5)
            page.mouse.click(fb["x"] + fb["width"]*0.4, fb["y"] + fb["height"]/2)
        time.sleep(2.5)
        hide_hint()
        # the fork: outcome strip (branch of ... + intervention), Villagers win
        strip = page.locator("#outcome-strip").bounding_box()
        if strip:
            sweep(page, [(strip["x"] + strip["width"]*0.15, strip["y"] + strip["height"]/2),
                         (strip["x"] + strip["width"]*0.6, strip["y"] + strip["height"]/2)], 4.0)
        page.evaluate("setStep(DATA.total_steps-1)")   # jump to the finished branch end
        time.sleep(4.5)
        segments.append({"i": 6, "start": s, "end": now()})
        mark("seg6 done")

        # --- 7. game family: resistance from the sidebar, ~11.3s (tts 10.2) ---
        s = now()
        page.evaluate("toggleSidebar()")
        time.sleep(1.5)
        card = page.locator(f'.game-card[data-gid="{RES_GAME}"]')
        try:
            card.scroll_into_view_if_needed(timeout=4000)
            cb = card.bounding_box()
            if cb:
                page.mouse.move(cb["x"] + cb["width"]/2, cb["y"] + cb["height"]/2, steps=25)
                time.sleep(1.3)
                page.mouse.click(cb["x"] + cb["width"]/2, cb["y"] + cb["height"]/2)
        except Exception:
            page.evaluate(f"switchGame('{RES_GAME}');closeSidebar()")
        time.sleep(2.6)
        hide_hint()
        page.evaluate("setStep(12)")   # mission proposals + team votes on screen
        # game-type badge in the header + transcript-only layout
        tb = page.locator("#game-type-badge").bounding_box()
        if tb:
            page.mouse.move(tb["x"] + tb["width"]/2, tb["y"] + tb["height"]/2, steps=25)
        time.sleep(4.9)
        segments.append({"i": 7, "start": s, "end": now()})
        mark("seg7 done")

        # --- 8. dashboard + language/models facets + light-theme flip, ~11.4s (tts 10.2) ---
        s = now()
        page.goto(f"{BASE}/dashboard.html")
        page.wait_for_load_state("networkidle", timeout=60000)
        page.evaluate(INJECT)
        time.sleep(1.5)
        # linger on the language / models facets before the curves
        for sel in ("#flt-lang", "#flt-model"):
            fb = page.locator(sel).bounding_box()
            if fb:
                page.mouse.move(fb["x"] + fb["width"] * 0.5,
                                fb["y"] + fb["height"] * 0.5, steps=25)
                time.sleep(1.1)
        # reveal the aggregate curves, then flip the lights on for the close
        page.mouse.wheel(0, 500)
        time.sleep(1.8)
        page.mouse.wheel(0, 400)
        time.sleep(1.4)
        page.mouse.wheel(0, -900)
        page.evaluate("toggleTheme()")
        time.sleep(1.5)
        segments.append({"i": 8, "start": s, "end": now()})
        mark("seg8 done")

        # --- 9. closing URL card (silent), ~5s ---
        s = now()
        page.mouse.move(W - 40, H - 30, steps=10)   # park the cursor off the card
        page.evaluate(URL_CARD)
        time.sleep(5.0)
        segments.append({"i": 9, "start": s, "end": now()})
        mark("seg9 done")

        ctx.close()
        path = page.video.path()
        browser.close()

    marks = {"trim": state["trim"], "segments": segments, "caps": CAPS, "webm": path}
    with open(os.path.join(OUT_DIR, "marks.json"), "w", encoding="utf-8") as f:
        json.dump(marks, f, ensure_ascii=False, indent=2)
    print("VIDEO:", path)
    print("MARKS:", os.path.join(OUT_DIR, "marks.json"))


if __name__ == "__main__":
    main()
