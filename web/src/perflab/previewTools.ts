// src/perflab/previewTools.ts
//
// Whether this build carries the development preview tools (the empty-state preview).
// Vite replaces `import.meta.env.DEV` with a literal at build time, so a production
// bundle sees `false` and drops the guarded UI. Its own module so tests can replace it.
export const PREVIEW_TOOLS: boolean = import.meta.env.DEV;
