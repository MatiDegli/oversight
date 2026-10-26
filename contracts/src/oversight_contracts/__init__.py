"""Oversight's wire contracts: the source of truth for every event, command and graph shape.

The public names live in four modules: `common` (ids, paths, timestamps, shared literals), `graph`,
`events` (the envelope, the eleven payloads and `ServerEvent`) and `commands` (request and response
bodies, and `ErrorResponse`). JSON Schema and TypeScript are generated from them by
`make contracts`. A change follows docs/proposal.md, section 4.9.
"""

CONTRACT_VERSION = "1.0.0"
