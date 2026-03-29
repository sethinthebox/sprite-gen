#!/usr/bin/env python3
"""Patch the sproAnimate function in index.html to use async job polling."""
import re

HTML_PATH = '/opt/sprite-gen/templates/index.html'
html = open(HTML_PATH).read()

old_fn = '''async function sproAnimate() {
  if (sproState.selectedIdx === null) {
    sproSetStatus('Please select a candidate first.', 'err');
    return;
  }
  const c = sproState.candidates[sproState.selectedIdx];
  const btn = document.getElementById('spro-animate-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Animating...';
  sproSetStatus('Generating 8 directions... this takes ~2-3 minutes.', 'info');

  try {
    const res = await fetch('/sprite/animate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_character: sproState.char,
        reference_sprite_url: c.url,
        actions: [sproState.action],
        sprite_size: 64
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Animation failed');

    // Show result
    document.getElementById('spro-animate-section').style.display = 'none';
    const gifUrl = data.gif_urls?.[sproState.action];
    if (gifUrl) {
      document.getElementById('spro-result-gif').src = gifUrl;
      document.getElementById('spro-gif-link').href = gifUrl;
    }
    if (data.sheet_url) {
      document.getElementById('spro-sheet-link').href = data.sheet_url;
    }
    document.getElementById('spro-result-section').style.display = 'block';
    sproSetStatus('Done! 🎉', 'info');
    btn.textContent = '🔄 Animate (8 Directions)';
    btn.disabled = false;
  } catch(e) {
    sproSetStatus('Error: ' + e.message, 'err');
    btn.textContent = '🔄 Animate (8 Directions)';
    btn.disabled = false;
  }
}'''

new_fn = '''async function sproAnimate() {
  if (sproState.selectedIdx === null) {
    sproSetStatus('Please select a candidate first.', 'err');
    return;
  }
  const c = sproState.candidates[sproState.selectedIdx];
  const btn = document.getElementById('spro-animate-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Starting job...';
  sproSetStatus('Starting 8-directional animation job...', 'info');
  document.getElementById('spro-animate-section').style.display = 'none';

  try {
    // ── 1. Start background job ────────────────────────────────────────────
    const startRes = await fetch('/sprite/animate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_character: sproState.char,
        reference_sprite_url: c.url,
        actions: [sproState.action],
        sprite_size: 64,
        seed: c.seed,        // pass selected candidate's seed for character consistency
      })
    });
    const startData = await startRes.json();
    if (!startRes.ok) throw new Error(startData.error || 'Failed to start job');

    const jobId = startData.job_id;
    sproSetStatus('Job ' + jobId + ' — generating 32 frames (~2-3 min)...', 'info');
    btn.textContent = '⏳ Animating...';

    // ── 2. Poll until done ─────────────────────────────────────────────────
    let pollCount = 0;
    const pollInterval = 4000; // 4s between polls

    await new Promise((resolve, reject) => {
      const poll = async () => {
        try {
          const statusRes = await fetch('/sprite/animate/status/' + jobId);
          const status = await statusRes.json();

          if (status.status === 'done') {
            // ── 3. Show result ─────────────────────────────────────────────
            document.getElementById('spro-animate-section').style.display = 'none';
            const resultRes = await fetch('/sprite/animate/result/' + jobId);
            const result = await resultRes.json();

            if (result.result && result.result.gif_urls) {
              const gifUrl = result.result.gif_urls[sproState.action];
              if (gifUrl) {
                document.getElementById('spro-result-gif').src = gifUrl;
                document.getElementById('spro-result-gif').style.display = 'block';
                document.getElementById('spro-gif-link').href = gifUrl;
              }
              if (result.result.sheet_url) {
                document.getElementById('spro-sheet-link').href = result.result.sheet_url;
                document.getElementById('spro-sheet-link').style.display = 'inline-block';
              }
            }

            document.getElementById('spro-result-section').style.display = 'block';
            sproSetStatus('Done! 🎉  ' + result.result.frames_generated + ' frames generated.', 'info');
            btn.textContent = '🔄 Animate (8 Directions)';
            btn.disabled = false;
            resolve();

          } else if (status.status === 'error') {
            sproSetStatus('Error: ' + (status.error || 'Job failed'), 'err');
            btn.textContent = '🔄 Animate (8 Directions)';
            btn.disabled = false;
            resolve();

          } else {
            // running — update progress
            const pct = status.progress ? status.progress.pct : 0;
            const current = status.progress ? status.progress.current : 0;
            const total = status.progress ? status.progress.total : 32;
            const elapsedMin = ((pollCount * pollInterval) / 60000).toFixed(1);
            btn.textContent = '⏳ ' + pct + '% (' + current + '/' + total + ')';
            sproSetStatus('Generating ' + current + '/' + total + ' frames — ' + elapsedMin + 'm elapsed...', 'info');
            pollCount++;
            setTimeout(poll, pollInterval);
          }
        } catch(e) {
          sproSetStatus('Poll error: ' + e.message, 'err');
          btn.textContent = '🔄 Animate (8 Directions)';
          btn.disabled = false;
          resolve();
        }
      };

      setTimeout(poll, 1000); // first poll after 1s
    });

  } catch(e) {
    sproSetStatus('Error: ' + e.message, 'err');
    btn.textContent = '🔄 Animate (8 Directions)';
    btn.disabled = false;
  }
}'''

if old_fn in html:
    html = html.replace(old_fn, new_fn)
    print('✓ Replaced sproAnimate with polling version')
else:
    print('WARNING: exact match not found — trying pattern match')
    # Try pattern-based replacement
    pattern = re.compile(r'async function sproAnimate\(\).*?\n}', re.DOTALL)
    if pattern.search(html):
        html = pattern.sub(new_fn, html, count=1)
        print('✓ Replaced sproAnimate via pattern match')
    else:
        print('ERROR: could not find sproAnimate to replace')

open(HTML_PATH, 'w').write(html)
print('Written:', HTML_PATH)
