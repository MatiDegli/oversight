# Oversight

Human oversight for coding agents.

Oversight is a visual IDE for Python projects where you supervise coding agents while they work. The
repository is shown as a live graph of modules, classes, and functions parsed from the source. Agents
built on Meta's Muse Spark, through the Meta Model API, edit the code, each in its own git worktree.
The functions an agent reads or edits light up in its color, and clicking one opens its live diff.
When a function changes, the graph shows its impact cone: every function that calls it, directly or
through others. Nothing reaches main until you approve it.

**Status:** in development during the Meta Global AI Developer Hackathon, from 2026-10-26 to
2026-11-04.

## Built during the hackathon

- The hackathon ran from 2026-10-26 at 12:00 ET to 2026-11-04 at 21:00 ET. All code in this repository
  was written in that window, and the git history shows it.
- Before the kickoff there were planning notes and UI sketches, and no code. What the project takes
  from them is written again here, under `docs/`.
- No code comes from other projects. Dependencies keep their own licenses: MIT, Apache-2.0, BSD, or
  ISC, and SIL OFL 1.1 for fonts. GPL projects were read for ideas only.
- Agents inside the product use only Meta models, through the Meta Model API.

## License

MIT. See [LICENSE](LICENSE).
