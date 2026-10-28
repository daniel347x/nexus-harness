# README screenshots

The Manager App’s current HTML, CSS and browser modules, on **invented system-development
records**, captured in an isolated, extension-free headless Chrome profile. These are
not live program receipts or measured performance results. No logged-in browser,
Workflowy account, daemon, Claude host or production Manager App is used.

| Image | Pixels | What it shows |
| --- | --- | --- |
| `desktop-hero.png` | 3200 × 2000 | Dictation, the held tool inspector, a Conductor/worker/helper tree, independent audits and the Manager conversation. |
| `program-view.png` | 3200 × 2000 | The same program tree beside its waves, split audits and human rollout decision in View. |
| `phone-programs.png` | 1170 × 2532 | `/m`’s Programs tab: running and favorite programs. |
| `phone-chat.png` | 1170 × 2532 | `/m`’s conversation, a HALT decision and an unsent reply. |

## Re-shoot

From this checkout, in WSL with Windows Chrome and Node installed:

```sh
bash docs/readme/capture.sh
# Replace selected images only:
bash docs/readme/capture.sh desktop-hero phone-chat
```

`NEXUS_MD_CHECKOUT` can name a NEXUS checkout containing `workflowy-glimpse-cache/`.
View uses that checkout’s actual Markdown renderer, Marked and KaTeX files;
it never loads the NEXUS example harness or its private catalogs.

The temporary HTTP origin serves the app’s real `index.html`, `sessions.html`,
`m.html` and `m-programs.html`. `fixture.js` supplies the made-up inventory,
conversation, tool snapshot, quota/telemetry values and View document. There are
no styling substitutes or banners; the one style rule `fixture.js` adds hides the
header's Claude account switcher, so the shots show no account names. The only source instrumentation exposes the
existing sidebar/phone controller for navigation; the app renders every widget.

`capture.cjs` uses Chrome’s DevTools protocol with desktop 1600 × 1000 CSS pixels
at 2×, and phone 390 × 844 CSS pixels with touch emulation at 3×. It selects the
Manager and the requested tab through the app’s controls. It captures the viewport
without browser chrome. Profiles and raw diagnostics live only under
`/mnt/c/tmp/companion-readme-shots/` and are removed after the run.
`capture-manifest.json` records the viewport, zero-error page diagnostics, PNG
hashes, source commit/tree and hashes of the actual UI/renderer files served.

## Privacy review

Every retained image was read as an image, including enlarged regional crops.
Visible content is limited to the Manager App, NEXUS/MCI, invented handles and
Manager-Session names, generic account labels and public model names. No personal
paths, hostnames/IPs, credentials, email addresses, private projects or private
Workflowy catalogs appear. No live session inventory was read for these captures.
