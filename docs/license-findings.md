# Runtime license findings

The maintainer approved the exact `typing-extensions` / `PSF-2.0` license exception
on 2026-10-27, as recorded in [DECISIONS.md](../DECISIONS.md#dependency-licenses).
The exception is implemented and `make check-notices` passes for the frozen runtime
inventory. No dependency was changed; all other packages remain subject to the
license policy. The package and its direct dependents are retained below for context.

| Package | Version | Installed license metadata | Direct runtime dependents |
| --- | --- | --- | --- |
| typing-extensions | 4.16.0 | PSF-2.0 | anyio 4.15.1; fastapi 0.143.0; httpx2 2.13.1; openai 3.29.0; opentelemetry-api 1.45.1; pydantic 2.14.1; pydantic-core 2.50.1; starlette 1.7.0; typing-inspection 0.4.4 |

Backend entry points reach it through FastAPI and OpenAI; contracts reach it
through Pydantic. The check prints the current direct dependents from installed
metadata, so this record cannot suppress a new failure.

The 2026-10-27 frozen Linux/Python 3.12 runtime inventory snapshot had 37 distinct package versions:
17 frontend and 20 Python. License declarations: MIT 23; BSD-3-Clause 6;
Apache-2.0 2; ISC 1; OFL-1.1 2; MIT OR Apache-2.0 1;
(MPL-2.0 OR Apache-2.0) 1; PSF-2.0 1. Both dual declarations pass
because an allowed option exists. OFL-1.1 is allowed only for the two named
Atkinson Fontsource packages. Unknown/ambiguous licenses fail closed.

Historical audit snapshot, 2026-10-27: `pnpm -C frontend audit` reported 5 vulnerabilities in
DOMPurify 3.4.15 through monaco-editor 0.57.0 (also through @monaco-editor/react): 1 critical,
1 high, 3 low. Current `THIRD_PARTY_NOTICES.md` lists DOMPurify 3.4.17. The old audit does not
establish the current vulnerability count; it was not rerun in this offline docs refresh.
The coordinator owns audit triage. The `make audit` target exists; pip-audit was neither
installed nor run for the historical front or this refresh.

`make notices` is offline and requires the already installed frozen locks. It
checks the installed pnpm application lock and Python versions before reading
metadata. Python runtime markers/extras are evaluated for that environment;
regenerate/check using the same platform as the committed inventory. The shared
uv workspace environment provides both backend and contracts metadata, and
`packaging` is already installed by the workspace's development tooling.

The generated notice includes upstream license texts and both font copyright
statements, with the common OFL text once. Monaco's additional third-party notice
is referenced by exact package version and also shipped as a plain file in the
static site. No UI link is added.
